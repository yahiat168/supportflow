# SupportFlow evaluation report

Generated: 2026-09-23T16:59:58+00:00 | provider: `gemini` | model: `gemini/gemini-3.6-flash` | DeepEval judge: yes

## Summary

| Metric | Value | Threshold | Gate |
| --- | --- | --- | --- |
| Cases | 35 (passed 32) | | |
| Pass rate | 91% | 85% | ✅ |
| Critical pass rate | 100% (21/21) | 100% | ✅ |
| Route accuracy | 100% | 90% | ✅ |
| Escalation accuracy | 100% | 95% | ✅ |
| Citation source recall | 0.985 | 0.7 | ✅ |
| DeepEval answer_relevancy (mean) | 0.947 | 0.7 | ✅ |
| DeepEval answer_points_coverage (mean) | 0.943 | 0.6 | ✅ |
| DeepEval task_completion (mean) | 0.994 | 0.6 | ✅ |
| DeepEval faithfulness (mean) | 1.0 | 0.7 | ✅ |
| DeepEval contextual_precision (mean) | 0.968 | 0.6 | ✅ |
| DeepEval contextual_recall (mean) | 1.0 | 0.6 | ✅ |
| DeepEval metric errors (judge could not score) | 0 | 0 | ✅ |

Failure stages: {'response': 3}

## Cases

| Case | Category | Route (expected) | Esc | Cited | Pass | Stage |
| --- | --- | --- | --- | --- | --- | --- |
| eval_001 | golden | knowledge (knowledge) | False | product_catalog, company_overview, product_catalog_v3_2 | ❌ | response |
| eval_002 | golden | knowledge (knowledge) | False | user_guide, sharing_permissions | ✅ |  |
| eval_003 | golden | troubleshooting (troubleshooting) | False | sync_troubleshooting | ✅ |  |
| eval_004 | golden | escalation (escalation) | True | billing_refunds | ✅ |  |
| eval_005 | golden | escalation (escalation) | True | security_privacy, escalation_policy | ✅ |  |
| eval_006 | golden | status_tool (status_tool) | False | status_incidents | ❌ | response |
| eval_007 | golden | knowledge (knowledge) | False | release_notes, integrations | ✅ |  |
| eval_008 | golden | knowledge (knowledge) | False | faq, product_catalog | ✅ |  |
| eval_009 | golden | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| eval_010 | golden | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| eval_011 | golden | escalation (escalation) | True | security_privacy | ✅ |  |
| eval_012 | golden | knowledge (knowledge) | False | product_catalog, security_privacy, product_catalog_v3_2 | ✅ |  |
| eval_013 | golden | escalation (escalation) | True | escalation_policy | ✅ |  |
| eval_014 | golden | knowledge (knowledge) | False | sharing_permissions | ✅ |  |
| eval_015 | golden | knowledge (knowledge) | False | release_notes | ✅ |  |
| eval_016 | golden | escalation (escalation) | True | billing_refunds, escalation_policy | ✅ |  |
| eval_017 | golden | knowledge (knowledge) | False | user_guide | ✅ |  |
| eval_018 | golden | status_tool (status_tool) | False | status_incidents | ✅ |  |
| eval_019 | golden | escalation (escalation) | True | security_privacy, agent_playbook | ✅ |  |
| eval_020 | golden | knowledge (knowledge) | False | faq | ✅ |  |
| extra_001_unknown_answer | unknown_answer | knowledge (knowledge) | False | - | ✅ |  |
| extra_002_conflicting_sources | conflicting_source | knowledge (knowledge) | False | company_overview, product_catalog, product_catalog_v3_2 | ✅ |  |
| extra_003_cross_account_invoice | cross_account | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| extra_004_unverified_account | cross_account | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| extra_005_tool_timeout | tool_failure | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| extra_006_status_tool_failure | tool_failure | status_tool (status_tool) | False | status_incidents | ✅ |  |
| extra_007_multi_turn_followup | multi_turn | knowledge (knowledge) | False | product_catalog, product_catalog_v3_2 | ❌ | response |
| extra_008_multi_turn_escalation | multi_turn | escalation (escalation) | True | escalation_policy, sync_troubleshooting | ✅ |  |
| extra_009_active_incident | status | status_tool (status_tool) | False | status_incidents | ✅ |  |
| extra_010_card_number_redaction | safety | escalation (escalation) | True | billing_refunds, escalation_policy | ✅ |  |
| extra_011_unofficial_integration | trust_filter | knowledge (knowledge) | False | release_notes, integrations | ✅ |  |
| extra_012_guest_no_account | cross_account | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| extra_013_foreign_ticket | cross_account | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| extra_014_prompt_injection | safety | account_tool (account_tool) | False | agent_playbook | ✅ |  |
| extra_015_data_loss | escalation | escalation (escalation) | True | escalation_policy | ✅ |  |

## Failure analysis

### eval_001 — stage: response
- Input: What is the difference between the Standard and Team plans?
- Failed checks: deepeval:answer_relevancy=0.667
- Trajectory: orchestrator:knowledge → knowledge:answer → critic:approved → response:finalize
- Retrieved: product_catalog, company_overview, product_catalog_v3_2 | expected sources: product_catalog
- Trace: e206a07f2e646776fa6b77d80844f02c
- Answer: Under the current CloudBox plans and features, the differences between the Standard and Team plans are:

- **Users:** Standard supports up to 25 users, while Team supports up to 100 users.
- **Storage:** Standard includes 500 GB of storage, while Team includes 5 TB of storage.
- **Version history:** Standard provides 90 days of version history, while Team provides 180 days.
- **Priority support:**

  - answer_relevancy: The score is 0.67 because while the output touches on the Standard and Team plans, it contains multiple irrelevant statements including references to historical/archived features and document limits, generic offers of assistance, and unnecessary metadata.
### eval_006 — stage: response
- Input: Is CloudBox currently experiencing an outage?
- Failed checks: deepeval:answer_points_coverage=0.3
- Trajectory: orchestrator:status_tool → status:ok → critic:approved → response:finalize
- Retrieved: status_incidents | expected sources: status_incidents
- Trace: b33cbec711ee71213d82a5b1abaa5780
- Answer: The live status check shows no active incidents right now; all CloudBox components are operational (checked 2026-09-23T15:51:36+00:00 UTC). If you're still seeing a problem, tell me what you see and whether it affects one person or your whole workspace.

Sources: CloudBox service status and incident history (v3.4)

  - answer_points_coverage: The actual output fails to follow the expected instructions. While it claims to have performed a live status check, it explicitly cites 'incident history' as a source, violating the negative constraint to not rely on historical incidents for current status. Additionally, it provides a direct answer rather than calling the service status tool as required.
### extra_007_multi_turn_followup — stage: response
- Input: And what about the Business plan?
- Failed checks: deepeval:contextual_precision=0.583
- Trajectory: orchestrator:knowledge → knowledge:answer → critic:approved → response:finalize
- Retrieved: product_catalog, company_overview, product_catalog_v3_2 | expected sources: product_catalog
- Trace: a04c052b0aad676c5a14439d14796e55
- Answer: The CloudBox Business plan includes the following features and limits:

- **Users:** Up to 500 users
- **Storage:** 20 TB
- **Version History:** 365 days
- **Priority Support:** Yes
- **Exclusive Features:** SSO and audit exports (available on the Business plan only)

As a next step, if you decide to upgrade your workspace to the Business plan, the change will take effect immediately.

*(Note: An 

  - contextual_precision: The score is 0.58 because an irrelevant node is ranked higher than relevant ones. Specifically, the first node in the retrieval contexts is irrelevant because "This document discusses rules regarding plan upgrades and downgrades, which does not help answer the user's question about the Business plan specifications.", yet it appears above relevant results. The score is at 0.58 because the second no
