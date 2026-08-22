# warehouse.tf: the analytics projection, and the schema that keeps speech out of it.
#
# The managed WarehouseExportPort adapter (adapters/gcp/warehouse.py) streams the flat
# ScorecardRow into project.dataset.table and refuses when the destination is unconfigured.
# This file creates that destination, in region and under CMEK, and pins its columns.
#
# The column list IS the control. SPEC.md: "The warehouse row carries no speech." An analytics
# table is joined, copied into notebooks and exported to spreadsheets by people who never saw
# the retention policy, so the evidence stays in the tenant-scoped Firestore store behind the
# 403 and only decided figures come here. Declaring the schema explicitly means a future column
# that could hold an utterance has to be added in a reviewable diff, rather than appearing the
# first time an exporter sends one.
#
# Principle map (COMPLIANCE.md):
#   P-03 (residency): the dataset location is var.region.
#   P-09: default CMEK on the dataset, with the BigQuery service-agent binding in kms.tf.
#   P-04 (minimise data): the schema mirrors domain/models.py ScorecardRow field for field.

resource "google_bigquery_dataset" "scorecards" {
  project                    = var.project_id
  dataset_id                 = local.warehouse_dataset_id
  friendly_name              = "E3 conversation QA scorecards"
  description                = "Flat, speech-free scorecard rows for QA analytics. Evidence stays in the tenant-scoped store."
  location                   = var.region # in-country analytics (P-03)
  delete_contents_on_destroy = false

  default_encryption_configuration {
    kms_key_name = google_kms_crypto_key.scorecard.id # CMEK (P-09)
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.bigquery,
  ]
}

resource "google_bigquery_table" "scorecards" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.scorecards.dataset_id
  table_id   = local.warehouse_table_id

  # A QA history is evidence; dropping the table has to be a deliberate act outside Terraform.
  deletion_protection = true

  description = "One row per scored contact. No field on this table can hold an utterance."

  time_partitioning {
    type  = "DAY"
    field = "as_of"
  }

  clustering = ["tenant", "market"]

  encryption_configuration {
    kms_key_name = google_kms_crypto_key.scorecard.id
  }

  schema = jsonencode([
    { name = "scorecard_id", type = "STRING", mode = "REQUIRED", description = "Digest of tenant, contact, pack, transcript, as_of and outcome." },
    { name = "tenant", type = "STRING", mode = "REQUIRED", description = "Owning tenant partition." },
    { name = "contact_id", type = "STRING", mode = "REQUIRED", description = "The graded contact." },
    { name = "market", type = "STRING", mode = "REQUIRED", description = "Market whose score pack applied." },
    { name = "pack_id", type = "STRING", mode = "REQUIRED", description = "Score pack identifier." },
    { name = "pack_version", type = "STRING", mode = "REQUIRED", description = "Score pack version the verdict was produced under." },
    { name = "as_of", type = "TIMESTAMP", mode = "REQUIRED", description = "The explicit as_of the engine scored against; a replay reproduces the verdict." },
    { name = "disposition", type = "STRING", mode = "REQUIRED", description = "Deterministic disposition." },
    { name = "severity", type = "STRING", mode = "REQUIRED", description = "Deterministic severity band." },
    { name = "disclosure_score", type = "FLOAT", mode = "REQUIRED", description = "Ratio computed by the engine." },
    { name = "adherence_score", type = "FLOAT", mode = "REQUIRED", description = "Ratio computed by the engine." },
    { name = "sentiment_score", type = "INTEGER", mode = "REQUIRED", description = "Summed cue score." },
    { name = "failing_requirement_ids", type = "STRING", mode = "REPEATED", description = "Requirement ids, never their wording." },
    { name = "vulnerability_cue_ids", type = "STRING", mode = "REPEATED", description = "Cue ids, never the utterance that matched." },
    { name = "requires_human_review", type = "BOOLEAN", mode = "REQUIRED", description = "Maker-checker flag (P-06)." },
    { name = "review_ref", type = "STRING", mode = "NULLABLE", description = "Reference returned by the Hrz7 console when the escalation was routed (R8)." },
  ])
}
