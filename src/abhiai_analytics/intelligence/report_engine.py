"""Deterministic, bounded Phase 5D structured report engine."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

from .business_insights import BusinessInsightEngine
from .integration import collect_analytics_context
from .models import Evidence, InsightCategory, Limitation
from .query_models import AnalyticsFact, BusinessQuestionResult, QueryStatus
from .report_models import (
    BusinessReport, DatasetIdentity, ReportChartSpec, ReportChartType,
    ReportDetailLevel, ReportFinding, ReportKPI, ReportSection, ReportStatus,
    ReportType, SectionType, utc_timestamp,
)
from .report_validator import validate_report
from .response_engine import BusinessResponseEngine
from .response_formatting import format_number
from .response_models import ResponseLanguage, ResponseMode

DETAIL_LIMITS = {
    ReportDetailLevel.EXECUTIVE: {"findings": 5, "dimensions": 1, "charts": 3, "categories": 7, "evidence": 30},
    ReportDetailLevel.STANDARD: {"findings": 10, "dimensions": 3, "charts": 6, "categories": 12, "evidence": 60},
    ReportDetailLevel.DETAILED: {"findings": 20, "dimensions": 5, "charts": 10, "categories": 20, "evidence": 100},
}


class BusinessReportEngine:
    def generate(self, manager, *, report_type="business_overview", detail_level="standard", language="english", dataset_id=None, response_service=None):
        started = time.perf_counter(); report_type = ReportType(report_type); detail = ReportDetailLevel(detail_level); language = ResponseLanguage(language); limits = DETAIL_LIMITS[detail]
        gather_start = time.perf_counter(); context = collect_analytics_context(manager, dataset_id, granularity="monthly"); insight_report = BusinessInsightEngine().analyze(dataset_id=context["dataset_id"], backend=context["backend"], kpis=context["kpis"], groups=context["groups"], time_rows=context["time_rows"], limitations=context["limitations"], metadata=context["metadata"]); gather_seconds = time.perf_counter()-gather_start
        required = {ReportType.SALES_PERFORMANCE: {"revenue"}, ReportType.FINANCIAL_PERFORMANCE: {"revenue", "cost", "profit"}}.get(report_type, set())
        missing = required - set(context["kpis"])
        limitations = list(insight_report.limitations)
        if missing: limitations.append(Limitation("unsupported_report_type", "Requested report requires: " + ", ".join(sorted(missing))))
        evidence_map = {}
        for insight in insight_report.insights:
            for evidence in insight.evidence: evidence_map[_eid(evidence)] = evidence
        evidence = tuple(list(evidence_map.values())[:limits["evidence"]]); allowed_eids = tuple(list(evidence_map)[:limits["evidence"]])
        kpis = tuple(ReportKPI(metric, float(value), format_number(value, percentage=metric=="margin"), "%" if metric=="margin" else None, None, (_find_kpi_eid(insight_report, metric),)) for metric, value in context["kpis"].items() if value is not None and _find_kpi_eid(insight_report, metric) in allowed_eids)
        priority_start=time.perf_counter(); findings = self._findings(insight_report, limits["findings"], allowed_eids); priority_seconds=time.perf_counter()-priority_start
        selected_insight_ids = {source_id for finding in findings for source_id in finding.source_insight_ids}
        summary_facts = [AnalyticsFact(f"Total {k.metric}", k.metric, k.raw_value) for k in kpis[:5]]
        for insight in insight_report.insights:
            if insight.id not in selected_insight_ids:
                continue
            dimension, dimension_value = next(iter(insight.dimensions.items()), (None, None))
            summary_facts.append(AnalyticsFact(insight.title, insight.metric, insight.current_value,
                dimension, dimension_value, insight.percentage_change, insight.period))
        summary_result = BusinessQuestionResult("Generate report summary", QueryStatus.ANSWERED, None, facts=tuple(summary_facts), evidence=evidence[:10], limitations=tuple(limitations), metadata={"currency": None})
        response_mode = {ReportDetailLevel.EXECUTIVE: ResponseMode.CONCISE, ReportDetailLevel.STANDARD: ResponseMode.STANDARD, ReportDetailLevel.DETAILED: ResponseMode.DETAILED}[detail]
        response_start=time.perf_counter(); response = BusinessResponseEngine().respond(summary_result, mode=response_mode, language=language.value, service=response_service); response_seconds=time.perf_counter()-response_start
        section_start=time.perf_counter(); sections = self._sections(response.answer, kpis, findings, limitations, allowed_eids); section_seconds=time.perf_counter()-section_start
        chart_start=time.perf_counter(); charts = self._charts(context, insight_report, limits, allowed_eids); chart_seconds=time.perf_counter()-chart_start
        record=manager.get_record(context["dataset_id"]); columns=sorted([*context["schema"].metrics.values(), *context["schema"].dimensions, *context["schema"].dates]); schema_hash=hashlib.sha256("|".join(columns).encode()).hexdigest()[:20]; generated=utc_timestamp(); identity=DatasetIdentity(record.id, record.info.path.name, int(context["metadata"]["row_count"]), context["backend"], schema_hash); report_id=hashlib.sha256(f"{record.id}|{report_type.value}|{detail.value}|{generated}".encode()).hexdigest()[:24]
        status=ReportStatus.INSUFFICIENT_DATA if missing else ReportStatus.PARTIAL if limitations else ReportStatus.COMPLETE
        metadata={"analytics_seconds":gather_seconds,"prioritization_seconds":priority_seconds,"section_seconds":section_seconds,"chart_seconds":chart_seconds,"response_seconds":response_seconds,"total_seconds":time.perf_counter()-started,"aggregate_rows":context["metadata"].get("aggregate_rows",0),"raw_rows":0,"bounds":limits,"llm_input_chars":response.metadata.get("llm_input_chars",0)}
        report=BusinessReport(report_id, {ReportType.BUSINESS_OVERVIEW:"Business Overview",ReportType.SALES_PERFORMANCE:"Sales Performance",ReportType.FINANCIAL_PERFORMANCE:"Financial Performance"}[report_type], report_type,status,generated,identity,None,language,detail,response.answer,kpis,sections,findings,charts,evidence,tuple(_dedupe(limitations)),tuple(item for finding in findings for item in finding.source_insight_ids),allowed_eids,response.generation_method,response.faithfulness_status,metadata)
        validation=validate_report(report); report=replace(report,generation_metadata={**metadata,"validation_errors":validation.errors,"valid":validation.valid})
        return report

    def _findings(self, report, limit, allowed):
        weights={InsightCategory.ANOMALY:100,InsightCategory.PROFITABILITY:90,InsightCategory.COMPARISON:80,InsightCategory.TREND:70,InsightCategory.CONTRIBUTION:60,InsightCategory.DIMENSION:50,InsightCategory.KPI:20}
        candidates=[]
        for insight in report.insights:
            if insight.category==InsightCategory.LIMITATION: continue
            eids=tuple(_eid(e) for e in insight.evidence if _eid(e) in allowed)
            if not eids: continue
            magnitude=abs(insight.percentage_change or 0); candidates.append((weights.get(insight.category,0)+min(magnitude,40),insight,eids))
        candidates.sort(key=lambda x:(-x[0],x[1].id))
        return tuple(ReportFinding(hashlib.sha256((i.id+"report").encode()).hexdigest()[:20],i.title,i.summary,i.category.value,"high" if score>=80 else "medium",(i.id,),eids) for score,i,eids in candidates[:limit])

    def _sections(self, summary,kpis,findings,limitations,eids):
        sections=[ReportSection(SectionType.EXECUTIVE_SUMMARY,"Executive Summary",summary,tuple(f.finding_id for f in findings[:3]),tuple(eids[:5]))]
        if kpis: sections.append(ReportSection(SectionType.KPI_OVERVIEW,"Key KPIs","Supported full-dataset KPIs.",(),tuple(e for k in kpis for e in k.source_evidence_ids)))
        metric_sections = (
            ("revenue", SectionType.REVENUE_ANALYSIS, "Revenue Analysis"),
            ("cost", SectionType.COST_ANALYSIS, "Cost Analysis"),
        )
        for metric, section_type, title in metric_sections:
            selected_kpis = [kpi for kpi in kpis if kpi.metric == metric]
            if selected_kpis:
                kpi = selected_kpis[0]
                sections.append(ReportSection(section_type, title, f"Measured {metric}: {kpi.formatted_value}.", (), kpi.source_evidence_ids))
        profitability_kpis = [kpi for kpi in kpis if kpi.metric in {"profit", "margin"}]
        if profitability_kpis:
            sections.append(ReportSection(SectionType.PROFITABILITY, "Profitability",
                "Supported profit and margin measures are available.", (),
                tuple(eid for kpi in profitability_kpis for eid in kpi.source_evidence_ids)))
        mapping={"trend":(SectionType.TREND_ANALYSIS,"Trend Analysis"),"dimension":(SectionType.DIMENSION_PERFORMANCE,"Dimension Performance"),"contribution":(SectionType.CONTRIBUTION_ANALYSIS,"Contribution Analysis"),"anomaly":(SectionType.ANOMALIES,"Unusual Signals")}
        for category,(stype,title) in mapping.items():
            selected=[f for f in findings if f.category==category]
            if selected: sections.append(ReportSection(stype,title," ".join(f.text for f in selected),tuple(f.finding_id for f in selected),tuple(e for f in selected for e in f.source_evidence_ids)))
        if limitations: sections.append(ReportSection(SectionType.LIMITATIONS,"Limitations"," ".join(x.message for x in limitations),(),()))
        return tuple(sections[:12])

    def _charts(self,context,report,limits,allowed):
        charts=[]
        # KPI cards are useful, but should not crowd out trend and comparison charts.
        for metric,value in list(context["kpis"].items())[:2]:
            eid=_find_kpi_eid(report,metric)
            if eid in allowed: charts.append(ReportChartSpec("kpi-"+metric,ReportChartType.KPI,metric.title(),metric,None,({"label":metric,"value":value},),"percentage" if metric=="margin" else "number",(eid,)))
        if context["time_rows"]:
            metric="revenue" if "revenue" in context["kpis"] else next(iter(context["kpis"]),None); eid=_find_category_eid(report,InsightCategory.TREND,metric)
            if metric and eid in allowed: charts.append(ReportChartSpec("trend-"+metric,ReportChartType.LINE,metric.title()+" over time",metric,"period",tuple({"period":r.get("period"),"value":r.get(metric)} for r in context["time_rows"][:120]),"number",(eid,)))
        for dim,rows in list(context["groups"].items())[:limits["dimensions"]]:
            metric="revenue" if "revenue" in context["kpis"] else next(iter(context["kpis"]),None); eid=_find_category_eid(report,InsightCategory.DIMENSION,metric,dim)
            if metric and eid in allowed:
                data=sorted(({"category":str(r.get("group_value")),"value":r.get(metric)} for r in rows if r.get(metric) is not None),key=lambda x:x["value"],reverse=True)[:limits["categories"]]
                charts.append(ReportChartSpec("bar-"+hashlib.sha1(dim.encode()).hexdigest()[:8],ReportChartType.BAR,metric.title()+" by "+dim,metric,dim,tuple(data),"number",(eid,)))
        return tuple(charts[:limits["charts"]])


def _eid(e): return hashlib.sha256(json.dumps({"d":e.description,"v":e.values,"m":e.method},sort_keys=True,default=str).encode()).hexdigest()[:20]
def _find_kpi_eid(report,metric):
    for i in report.insights:
        if i.category==InsightCategory.KPI and i.metric==metric and i.evidence:return _eid(i.evidence[0])
    return ""
def _find_category_eid(report,category,metric,dimension=None):
    for i in report.insights:
        if i.category==category and i.metric==metric and (dimension is None or dimension in i.dimensions) and i.evidence:return _eid(i.evidence[0])
    return ""
def _dedupe(items): return list({x.code:x for x in items}.values())
