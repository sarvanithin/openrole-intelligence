"""Deterministic connectors for public first-party ATS job feeds."""

from fortune_intel.connectors.adp import (
    ADPPolicyHeldCandidate,
    ADPPolicyProbe,
    classify_adp_board_url,
    probe_adp_policy,
)
from fortune_intel.connectors.adp_workforce_now import (
    ADPWorkforceNowConnector,
    ADPWorkforceNowSource,
    adp_workforce_now_source,
    adp_workforce_now_source_from_url,
    parse_adp_workforce_now_source_key,
)
from fortune_intel.connectors.amazon_jobs import AmazonJobsConnector
from fortune_intel.connectors.apple_jobs import AppleJobsConnector
from fortune_intel.connectors.ashby import AshbyConnector
from fortune_intel.connectors.avature import (
    AvaturePolicyHeldCandidate,
    AvaturePolicyProbe,
    classify_avature_board_url,
    probe_avature_policy,
)
from fortune_intel.connectors.dayforce import (
    DayforcePolicyHeldCandidate,
    DayforcePolicyProbe,
    classify_dayforce_board_url,
    probe_dayforce_policy,
)
from fortune_intel.connectors.eightfold import (
    EightfoldPolicyHeldCandidate,
    EightfoldPolicyProbe,
    classify_eightfold_board_url,
    probe_eightfold_policy,
)
from fortune_intel.connectors.factory import build_connector
from fortune_intel.connectors.greenhouse import GreenhouseConnector
from fortune_intel.connectors.http import HttpFailure, JsonHttpClient
from fortune_intel.connectors.icims import (
    ICIMSPolicyHeldConnector,
    ICIMSSource,
    classify_icims_board_url,
    icims_source,
    parse_icims_source_key,
)
from fortune_intel.connectors.icims_public import (
    ICIMSPublicConnector,
    icims_public_source_from_url,
)
from fortune_intel.connectors.jobvite import (
    JobvitePolicyHeldCandidate,
    JobvitePolicyProbe,
    classify_jobvite_board_url,
    probe_jobvite_policy,
)
from fortune_intel.connectors.lever import LeverConnector
from fortune_intel.connectors.models import (
    ConnectorError,
    ConnectorJob,
    ConnectorResult,
)
from fortune_intel.connectors.official_structured import (
    OfficialStructuredConnector,
    OfficialStructuredHttpClient,
    OfficialStructuredSource,
    TextResponse,
    official_structured_source,
    parse_official_structured_source_key,
)
from fortune_intel.connectors.oracle_recruiting import (
    OracleRecruitingConnector,
    OracleRecruitingSource,
    oracle_recruiting_source,
    parse_oracle_recruiting_source_key,
)
from fortune_intel.connectors.paycom import (
    PaycomPolicyHeldCandidate,
    PaycomPolicyProbe,
    classify_paycom_board_url,
    probe_paycom_policy,
)
from fortune_intel.connectors.paycor import (
    PaycorPolicyHeldCandidate,
    PaycorPolicyProbe,
    classify_paycor_board_url,
    probe_paycor_policy,
)
from fortune_intel.connectors.paylocity import (
    PaylocityPolicyHeldCandidate,
    PaylocityPolicyProbe,
    classify_paylocity_board_url,
    probe_paylocity_policy,
)
from fortune_intel.connectors.rippling import (
    RipplingPolicyHeldCandidate,
    RipplingPolicyProbe,
    classify_rippling_board_url,
    probe_rippling_policy,
)
from fortune_intel.connectors.smartrecruiters import SmartRecruitersConnector
from fortune_intel.connectors.successfactors import (
    SuccessFactorsPolicyHeldCandidate,
    SuccessFactorsPolicyProbe,
    classify_successfactors_board_url,
    probe_successfactors_policy,
)
from fortune_intel.connectors.taleo import (
    TaleoPolicyHeldCandidate,
    TaleoPolicyProbe,
    classify_taleo_board_url,
    probe_taleo_policy,
)
from fortune_intel.connectors.ukg import (
    UKGPolicyHeldCandidate,
    UKGPolicyProbe,
    classify_ukg_board_url,
    probe_ukg_policy,
)
from fortune_intel.connectors.ukg_recruiting_public import (
    UKGRecruitingPublicConnector,
    UKGRecruitingPublicSource,
    parse_ukg_recruiting_public_source_key,
    ukg_recruiting_public_source,
    ukg_recruiting_public_source_from_url,
)
from fortune_intel.connectors.workday import (
    WorkdayConnector,
    WorkdaySource,
    parse_workday_source_key,
    workday_source,
)

__all__ = [
    "ADPPolicyHeldCandidate",
    "ADPPolicyProbe",
    "ADPWorkforceNowConnector",
    "ADPWorkforceNowSource",
    "AmazonJobsConnector",
    "AppleJobsConnector",
    "AshbyConnector",
    "AvaturePolicyHeldCandidate",
    "AvaturePolicyProbe",
    "ConnectorError",
    "ConnectorJob",
    "ConnectorResult",
    "DayforcePolicyHeldCandidate",
    "DayforcePolicyProbe",
    "EightfoldPolicyHeldCandidate",
    "EightfoldPolicyProbe",
    "GreenhouseConnector",
    "HttpFailure",
    "ICIMSPolicyHeldConnector",
    "ICIMSPublicConnector",
    "ICIMSSource",
    "JobvitePolicyHeldCandidate",
    "JobvitePolicyProbe",
    "JsonHttpClient",
    "LeverConnector",
    "OfficialStructuredConnector",
    "OfficialStructuredHttpClient",
    "OfficialStructuredSource",
    "OracleRecruitingConnector",
    "OracleRecruitingSource",
    "PaycomPolicyHeldCandidate",
    "PaycomPolicyProbe",
    "PaycorPolicyHeldCandidate",
    "PaycorPolicyProbe",
    "PaylocityPolicyHeldCandidate",
    "PaylocityPolicyProbe",
    "RipplingPolicyHeldCandidate",
    "RipplingPolicyProbe",
    "SmartRecruitersConnector",
    "SuccessFactorsPolicyHeldCandidate",
    "SuccessFactorsPolicyProbe",
    "TaleoPolicyHeldCandidate",
    "TaleoPolicyProbe",
    "TextResponse",
    "UKGPolicyHeldCandidate",
    "UKGPolicyProbe",
    "UKGRecruitingPublicConnector",
    "UKGRecruitingPublicSource",
    "WorkdayConnector",
    "WorkdaySource",
    "adp_workforce_now_source",
    "adp_workforce_now_source_from_url",
    "build_connector",
    "classify_adp_board_url",
    "classify_avature_board_url",
    "classify_dayforce_board_url",
    "classify_eightfold_board_url",
    "classify_icims_board_url",
    "classify_jobvite_board_url",
    "classify_paycom_board_url",
    "classify_paycor_board_url",
    "classify_paylocity_board_url",
    "classify_rippling_board_url",
    "classify_successfactors_board_url",
    "classify_taleo_board_url",
    "classify_ukg_board_url",
    "icims_public_source_from_url",
    "icims_source",
    "official_structured_source",
    "oracle_recruiting_source",
    "parse_adp_workforce_now_source_key",
    "parse_icims_source_key",
    "parse_official_structured_source_key",
    "parse_oracle_recruiting_source_key",
    "parse_ukg_recruiting_public_source_key",
    "parse_workday_source_key",
    "probe_adp_policy",
    "probe_avature_policy",
    "probe_dayforce_policy",
    "probe_eightfold_policy",
    "probe_jobvite_policy",
    "probe_paycom_policy",
    "probe_paycor_policy",
    "probe_paylocity_policy",
    "probe_rippling_policy",
    "probe_successfactors_policy",
    "probe_taleo_policy",
    "probe_ukg_policy",
    "ukg_recruiting_public_source",
    "ukg_recruiting_public_source_from_url",
    "workday_source",
]
