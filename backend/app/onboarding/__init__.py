"""Adaptive unknown-vendor onboarding.

AI is used for ONBOARDING (suggesting a declarative adapter from samples);
deterministic, human-approved configuration is used at RUNTIME. Nothing in
this package is imported by the ingestion pipeline except through the
approved-adapter registry built by app.services.onboarding_service.
"""
