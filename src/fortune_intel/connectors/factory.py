"""Generic construction helper for scheduler and ingestion integrations."""

from __future__ import annotations

from fortune_intel.connectors.adp_workforce_now import ADPWorkforceNowConnector
from fortune_intel.connectors.amazon_jobs import AmazonJobsConnector
from fortune_intel.connectors.apple_jobs import AppleJobsConnector
from fortune_intel.connectors.ashby import AshbyConnector
from fortune_intel.connectors.greenhouse import GreenhouseConnector
from fortune_intel.connectors.http import JsonHttpClient
from fortune_intel.connectors.icims import ICIMSPolicyHeldConnector
from fortune_intel.connectors.icims_public import ICIMSPublicConnector
from fortune_intel.connectors.lever import LeverConnector
from fortune_intel.connectors.official_structured import OfficialStructuredConnector
from fortune_intel.connectors.oracle_recruiting import OracleRecruitingConnector
from fortune_intel.connectors.smartrecruiters import SmartRecruitersConnector
from fortune_intel.connectors.ukg_recruiting_public import UKGRecruitingPublicConnector
from fortune_intel.connectors.workday import WorkdayConnector

Connector = (
    ADPWorkforceNowConnector
    | AmazonJobsConnector
    | AppleJobsConnector
    | AshbyConnector
    | GreenhouseConnector
    | ICIMSPolicyHeldConnector
    | ICIMSPublicConnector
    | LeverConnector
    | OracleRecruitingConnector
    | OfficialStructuredConnector
    | SmartRecruitersConnector
    | UKGRecruitingPublicConnector
    | WorkdayConnector
)


def build_connector(
    kind: str,
    board_token: str,
    client: JsonHttpClient | None = None,
) -> Connector:
    """Build a supported connector or a fail-closed policy-held probe."""

    normalized = kind.strip().casefold().replace("-", "").replace("_", "")
    if normalized == "adp":
        raise ValueError(
            "generic ADP connector is policy-held: the official Job Requisitions API "
            "requires Consumer Application Registry scope; only exact anonymous public "
            "Workforce Now career-center URLs use the adp_workforce_now connector"
        )
    if normalized in {"avature", "avatureats"}:
        raise ValueError(
            "Avature connector is policy-held: official integrations use "
            "customer-admin-configured custom endpoints and vendor credentials/API keys; "
            "no authorized anonymous standardized complete-manifest API has been verified"
        )
    if normalized in {"ceridian", "dayforce", "dayforcehcm"}:
        raise ValueError(
            "Dayforce connector is policy-held: the official API agreement requires explicit, "
            "verifiable client consent and no authorized anonymous complete-manifest contract "
            "has been verified"
        )
    if normalized == "eightfold":
        raise ValueError(
            "Eightfold connector is policy-held: official Position APIs require tenant "
            "authentication and no anonymous complete-manifest API has been verified"
        )
    if normalized in {"sapsf", "sapsuccessfactors", "successfactors"}:
        raise ValueError(
            "SAP SuccessFactors connector is policy-held: Recruiting OData requires registered "
            "OAuth credentials and Recruiting permissions, and no anonymous complete-manifest "
            "API has been verified"
        )
    if normalized in {"paylocity", "paylocityrecruiting"}:
        raise ValueError(
            "Paylocity connector is policy-held: APIs require bearer authentication and "
            "client-specific production authorization, and no anonymous Recruiting "
            "complete-manifest API has been verified"
        )
    if normalized in {"rippling", "ripplingats"}:
        raise ValueError(
            "Rippling ATS connector is policy-held: supported APIs require a company-bound "
            "API key or OAuth token and no anonymous ATS complete-manifest API has been verified"
        )
    if normalized in {"ukg", "ukgpro", "ukgprorecruiting", "ultipro", "ultiprorecruiting"}:
        raise ValueError(
            "UKG/UltiPro connector is policy-held: official developer access requires "
            "administrator-issued credentials, no anonymous complete-manifest API has been "
            "verified, and UKG terms prohibit unauthorized automated scraping/access"
        )
    if normalized in {
        "oracletaleo",
        "taleo",
        "taleobusinessedition",
        "taleoenterprise",
        "tbe",
    }:
        raise ValueError(
            "Oracle Taleo connector is policy-held: supported APIs require tenant-specific "
            "service access and credentials, no anonymous complete-manifest API has been "
            "verified, and Oracle terms require express written permission for automated access"
        )
    if normalized in {"employjobvite", "jobvite", "jobviteats"}:
        raise ValueError(
            "Jobvite connector is policy-held: official terms prohibit scraping and "
            "third-party redistribution of Job Postings, no anonymous complete-manifest API "
            "has been verified, and official integrations use customer-enabled access"
        )
    if normalized in {"paycom", "paycomats", "paycomonline"}:
        raise ValueError(
            "Paycom connector is policy-held: official terms prohibit automated extraction "
            "or scraping without written authorization, documented Data Services require "
            "Paycom-owned access keys, and no anonymous complete-manifest API has been verified"
        )
    if normalized in {"paycor", "paycorrecruiting", "recruitingbypaycor"}:
        raise ValueError(
            "Paycor Recruiting connector is policy-held: official ATS APIs require an APIM "
            "subscription key, OAuth token, and client-admin activation; Paycor terms prohibit "
            "unapproved automated scraping; no anonymous complete-manifest API was verified"
        )
    factories = {
        "adpworkforcenow": ADPWorkforceNowConnector,
        "amazonjobs": AmazonJobsConnector,
        "applejobs": AppleJobsConnector,
        "ashby": AshbyConnector,
        "greenhouse": GreenhouseConnector,
        "icims": ICIMSPolicyHeldConnector,
        "icimspublic": ICIMSPublicConnector,
        "lever": LeverConnector,
        "oraclerecruiting": OracleRecruitingConnector,
        "officialstructured": OfficialStructuredConnector,
        "smartrecruiters": SmartRecruitersConnector,
        "ukgrecruitingpublic": UKGRecruitingPublicConnector,
        "workday": WorkdayConnector,
    }
    connector_type = factories.get(normalized)
    if connector_type is None:
        supported = ", ".join(sorted(factories))
        raise ValueError(f"unsupported connector kind {kind!r}; expected one of: {supported}")
    return connector_type(board_token, client=client)
