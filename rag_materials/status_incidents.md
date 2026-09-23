---
doc_id: status_incidents
title: CloudBox service status and incident history
product: CloudBox
version: 3.4
source_type: incident_log
trust_level: official
---

# Service status and incident history

The status tool is the source of truth for the current service state. The knowledge base contains historical incidents only and must not be used to claim that a current incident is active.

Historical incident INC-2026-014 occurred on 2026-02-11 and affected file previews for 42 minutes. Historical incident INC-2026-021 occurred on 2026-03-03 and delayed email notifications for 18 minutes. Both incidents are resolved.

If the user reports that many workspace members are affected, the assistant should call the service status tool. If the status tool reports an active incident, explain the affected component and provide the latest status message. Do not invent an estimated recovery time.
