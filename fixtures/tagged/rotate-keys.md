---
title: Rotating access keys
doc_id: SEC-001
kind: guide
product:
  name: Beacon
  tier: enterprise
audience:
  - engineering
  - security
published: 2026-02-11
owner: maintainers
---
# Rotating access keys

## Summary

Rotate access keys every 90 days and revoke the old key after the cutover.

## Details

Automation rotates keys at night and posts the result to the audit channel.
