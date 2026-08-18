"""Persistence adapters."""

from fortune_intel.storage.acquisition_ops import AcquisitionTaskSeed
from fortune_intel.storage.sqlite import JobRepository

__all__ = ["AcquisitionTaskSeed", "JobRepository"]
