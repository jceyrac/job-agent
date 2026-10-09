"""ATS adapters — company-keyed scrapers driven by a list of company identifiers.
Each adapter is a pure function (list of targets → list of jobs) with no internal
mode logic.  Targets are supplied by the orchestrator: all known companies in
discovery mode, or only monitored=True companies in monitoring mode.

See constitution Principle VIII (Scraper taxonomy, discovery/monitoring split).
"""
