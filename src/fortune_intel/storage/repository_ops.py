"""Composed operation mixins for the SQLite repository."""

from fortune_intel.storage.acquisition_ops import AcquisitionOperationsMixin
from fortune_intel.storage.approval_ops import ApprovalOperationsMixin
from fortune_intel.storage.company_ops import CompanyOperationsMixin
from fortune_intel.storage.coverage_audit_ops import CoverageAuditOperationsMixin
from fortune_intel.storage.coverage_ops import CoverageOperationsMixin
from fortune_intel.storage.detail_ops import DetailOperationsMixin
from fortune_intel.storage.employer_ops import EmployerOperationsMixin
from fortune_intel.storage.fingerprint_ops import FingerprintOperationsMixin
from fortune_intel.storage.job_ops import JobOperationsMixin
from fortune_intel.storage.manifest_ops import ManifestOperationsMixin
from fortune_intel.storage.priority_ops import PriorityOperationsMixin
from fortune_intel.storage.source_ops import SourceOperationsMixin
from fortune_intel.storage.summary_ops import SummaryOperationsMixin


class RepositoryOperations(
    AcquisitionOperationsMixin,
    ApprovalOperationsMixin,
    SourceOperationsMixin,
    ManifestOperationsMixin,
    JobOperationsMixin,
    CompanyOperationsMixin,
    CoverageOperationsMixin,
    FingerprintOperationsMixin,
    CoverageAuditOperationsMixin,
    DetailOperationsMixin,
    EmployerOperationsMixin,
    PriorityOperationsMixin,
    SummaryOperationsMixin,
):
    """Aggregate stateless storage operations without adding behavior."""
