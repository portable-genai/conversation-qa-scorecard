# apis.tf: enable exactly the managed services this stack depends on.
#
# Principle map (COMPLIANCE.md):
#   P-01 (managed-first, minimal surface): only the services the pinned stack actually uses
#         are enabled. Every entry below is here because a bound gcp adapter calls it; nothing
#         is speculative.
#   P-03 (residency): enabling these is the prerequisite for the regional, CMEK-protected
#         resources the sibling files create.
#
# disable_on_destroy = false, so destroying this stack does not yank platform APIs out from
# under other workloads in a shared project.

locals {
  required_services = [
    # Called by a bound adapter (src/conversation_qa_scorecard/adapters/gcp/).
    "aiplatform.googleapis.com", # narration.py and signals.py (Gemini restates, never decides)
    "speech.googleapis.com",     # transcription.py (batch recogniser, word offsets)
    "firestore.googleapis.com",  # scorecard_store.py (tenant-scoped evidence)
    "bigquery.googleapis.com",   # warehouse.py (the flat row that carries no speech)
    "logging.googleapis.com",    # audit.py (the WORM audit sink, rule R2)
    "cloudtrace.googleapis.com", # tracer.py (spans, content off)
    "monitoring.googleapis.com", # log-based metrics and the security alert policies
    "run.googleapis.com",        # the serving edge
    "secretmanager.googleapis.com",
    "storage.googleapis.com",  # the call-recording bucket the recogniser reads
    "cloudkms.googleapis.com", # the regional CMEK key ring
    "iap.googleapis.com",      # the identity edge the one VERIFIED adapter checks against

    # Supporting services the above require.
    "accesscontextmanager.googleapis.com", # the VPC-SC perimeter (P-03)
    "compute.googleapis.com",              # the external load balancer and Cloud Armor
    "iam.googleapis.com",                  # least-privilege service accounts
    "orgpolicy.googleapis.com",            # the residency and key-hygiene constraints (P-03)
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
