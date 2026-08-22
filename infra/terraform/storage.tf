# storage.tf: the in-region bucket the batch recogniser reads call audio from.
#
# The managed TranscriptSourcePort adapter (adapters/gcp/transcription.py) refuses a contact
# that carries no audio_uri rather than scoring an empty conversation, so a deployment needs a
# governed place for that audio to live. This is it: regional, CMEK-encrypted, uniform access,
# public access prevented, and not force-destroyable.
#
# Principle map (COMPLIANCE.md):
#   P-03 (residency): location is var.region. A call recording is the rawest personal data
#         this service touches, and it never leaves the region.
#   P-09: CMEK, with the Cloud Storage service-agent binding in kms.tf. Uniform bucket-level
#         access removes per-object ACLs entirely (org_policy.tf enforces that project-wide).
#   P-04: nothing in this bucket is read by a model. The recogniser produces a transcript, the
#         domain masks it, and only the masked text is ever matched, scored or narrated.
#
# Retention and deletion of the recordings themselves are the adopter's schedule to set, so no
# lifecycle rule is imposed here. The audit trail of what was DONE with them is separate, and
# that one is locked (logging_worm.tf).

resource "google_storage_bucket" "recordings" {
  name                        = local.recordings_bucket_name
  project                     = var.project_id
  location                    = var.region # in-country audio (P-03)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.scorecard.id # CMEK (P-09)
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.storage,
  ]
}

# The serving identity reads recordings and never writes them: this service grades calls, it
# does not record them. A write role here would let a scored conversation be replaced by a
# different one after the fact.
resource "google_storage_bucket_iam_member" "app_recordings_reader" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.app.email}"
}
