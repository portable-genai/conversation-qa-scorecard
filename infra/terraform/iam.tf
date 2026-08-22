# iam.tf: the least-privilege serving identity.
#
# Principle map (COMPLIANCE.md):
#   P-09 (defence in depth, least privilege): one serving identity that holds only the roles
#         the request pipeline needs (fetch a transcript, read the recording, persist a
#         scorecard, export the flat row, write audit and traces, call the narration model,
#         read its own secrets). No shared kitchen-sink account and no primitive roles.
#   P-03 (residency): the identity is project-scoped and every service it reaches is regional.
#   P-06 / R8: routing an escalation to the Hrz7 console is an outbound HTTPS call carrying a
#         service credential from Secret Manager, not a GCP IAM role, so nothing is granted
#         for it here.
#
# There is deliberately ONE service account. Doc1 carries a second identity for its Agent
# Runtime; this repo's agent surface is a set of plain tool callables that run inside the same
# process as the API (nothing in agent/ needs a runtime to import), so a second identity would
# have nothing to attach to and would only widen what is provisioned. Add one in the same
# commit that deploys the agent somewhere else, never before.

resource "google_service_account" "app" {
  account_id   = local.app_sa_id
  display_name = "E3 Conversation QA and Compliance Scorecard (serving / API)"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Every role below is traceable to a bound adapter. aiplatform.user covers the narration and
  # signal models, which restate and classify and never produce a number or a verdict.
  app_roles = [
    "roles/aiplatform.user",              # narration.py, signals.py
    "roles/speech.client",                # transcription.py (batch recognise)
    "roles/datastore.user",               # scorecard_store.py (no datastore.owner)
    "roles/bigquery.dataEditor",          # warehouse.py (insert rows, not manage the project)
    "roles/bigquery.jobUser",             # the streaming insert needs a job in this project
    "roles/logging.logWriter",            # audit.py (write only: it cannot read the WORM trail)
    "roles/cloudtrace.agent",             # tracer.py
    "roles/secretmanager.secretAccessor", # the inbound and outbound service credentials
  ]
}

resource "google_project_iam_member" "app" {
  for_each = toset(local.app_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app.email}"
}

# The app uses the CMEK for the envelope operations it performs directly.
resource "google_kms_crypto_key_iam_member" "app" {
  crypto_key_id = google_kms_crypto_key.scorecard.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.app.email}"
}
