# providers.tf: provider pinning and state for the E3 Conversation QA and Compliance Scorecard.
#
# Principle map (COMPLIANCE.md):
#   P-03 (single region / residency): every provider call is pinned to var.region, which is
#         SELECTED AT DEPLOY TIME and validated against an allowlist (variables.tf). There is
#         no global or multi-region default; the default is asia-southeast1, the one region
#         this service is licensed to hold conversation evidence in.
#   P-02 (no lock-in): Terraform is the only place infrastructure is described. The app talks
#         to ports, never to these resources.
#
# The GA google provider covers everything but one resource: google_model_armor_template
# (model_armor.tf, rule R1) is google-beta only on the pinned provider version. A vertical that
# adds another beta-only resource keeps using this same provider rather than adding a third.

terraform {
  required_version = ">= 1.9.0"

  # Partial backend. `terraform init -backend-config=...` supplies the reviewed state bucket
  # and the per-installation prefix, so neither is committed here and the module stays
  # reusable across installations. Keeping the declaration active is what makes accidental
  # local state impossible in a real runner; `-backend=false` is for offline validation only.
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region # the selected, allowlisted region: pinned, never global
}

provider "google-beta" {
  project = var.project_id
  region  = var.region # the selected, allowlisted region: pinned, never global
}
