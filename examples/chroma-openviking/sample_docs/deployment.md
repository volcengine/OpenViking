# Deployment Notes

The shared OpenViking service listens on port 1933.

Operators should prefer HTTP server mode for multi-session workloads instead of repeatedly
starting local embedded instances.

During incidents, the first health checks are:
- API reachability
- queue backlog
- embedding provider readiness
