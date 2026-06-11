# Incident Runbook

When an incident is declared, page the on-call engineer and open a ticket with
severity level. Check gateway latency dashboards first, then backend server
health. Rollback procedure: redeploy the previous container image tag and
verify metrics return to baseline within fifteen minutes.
