# production_edge.tftest.hcl: the posture claims, as executable tests.
#
# Matches the reference stack, adapted for this repo. Every run below uses `mock_provider`, so the
# whole file runs with NO credentials, NO project and NO network beyond the provider download:
#   terraform init -backend=false && terraform test
# The Doc1 runs that were NOT portable are the Mode 5 signing-key stages and the installation
# manifest contract; those are its embedded-grant browser flow, which this service does not
# have, and their module does not exist here.
#
# What these runs are for: a residency or fail-closed claim that only lives in a comment is a
# claim nobody checks. Each `expect_failures` run proves that a specific misconfiguration is
# refused at plan time rather than reaching an apply.

mock_provider "google" {}

run "residency_defaults_are_in_country" {
  command = plan

  variables {
    project_id    = "fictional-convqa-sg"
    enable_vpc_sc = false
  }

  assert {
    condition     = var.region == "asia-southeast1" && var.allowed_regions == tolist(["asia-southeast1"])
    error_message = "The default region and residency allowlist must both stay asia-southeast1."
  }

  assert {
    condition     = google_kms_key_ring.scorecard.location == var.region
    error_message = "CMEK key material must be regional and in the deployment region, never a multi-region ring."
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.location == var.region
    error_message = "The WORM audit bucket must be created in the deployment region."
  }

  assert {
    condition     = google_bigquery_dataset.scorecards.location == var.region
    error_message = "The analytics dataset must be created in the deployment region."
  }

  assert {
    condition     = google_storage_bucket.recordings.location == var.region
    error_message = "Call recordings must be created in the deployment region."
  }

  assert {
    condition     = one(google_firestore_database.scorecards[*].location_id) == var.region
    error_message = "The scorecard store must be created in the deployment region."
  }

  assert {
    condition     = one(google_org_policy_policy.resource_locations[*].spec[0].rules[0].values[0].allowed_values) == tolist(["in:asia-southeast1-locations"])
    error_message = "The org-policy location allowlist must pin exactly the deployment region's location group."
  }

  assert {
    condition     = one(google_org_policy_policy.disable_sa_keys[*].spec[0].rules[0].enforce) == "TRUE"
    error_message = "Service-account key creation must stay forbidden: an exported key is a credential that leaves the perimeter in a file."
  }

  assert {
    condition     = var.worm_locked && var.retention_days == 180
    error_message = "The audit bucket must stay locked at the six-month retention floor by default."
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.locked
    error_message = "The WORM lock must be applied to the bucket, not merely defaulted in a variable."
  }
}

run "perimeter_starts_in_dry_run" {
  command = plan

  variables {
    project_id       = "fictional-convqa-sg"
    access_policy_id = "123456789012"
  }

  assert {
    condition     = google_access_context_manager_service_perimeter.scorecard[0].use_explicit_dry_run_spec
    error_message = "The perimeter must start in dry run: never enforce blind on a path nobody has watched."
  }

  assert {
    condition     = length(google_access_context_manager_service_perimeter.scorecard[0].status[0].restricted_services) == 0
    error_message = "In dry run the enforced status must stay open; the restricted services belong in the dry-run spec."
  }

  assert {
    condition     = contains(google_access_context_manager_service_perimeter.scorecard[0].spec[0].restricted_services, "speech.googleapis.com")
    error_message = "The dry-run spec must audit the speech API: a transcript is the rawest conversation data crossing the boundary."
  }
}

run "default_omits_the_serving_edge" {
  command = plan

  variables {
    project_id    = "fictional-convqa-sg"
    enable_vpc_sc = false
  }

  assert {
    condition     = length(google_cloud_run_v2_service.api) == 0 && length(google_compute_global_forwarding_rule.edge) == 0
    error_message = "The serving edge must be opt-in, so the residency and audit stack can be applied and reviewed before anything serves."
  }
}

run "serving_edge_contract" {
  command = plan

  # A mocked provider leaves every computed attribute unknown at plan time, and the CMEK key
  # id is one. Overriding it during the plan is what lets the run assert that the revision is
  # bound to THAT key rather than to nothing; the value here is a stand-in for a real key id
  # and is never applied anywhere.
  override_resource {
    target          = google_kms_crypto_key.scorecard
    override_during = plan
    values = {
      id = "projects/fictional-convqa-sg/locations/asia-southeast1/keyRings/convqa-scorecard-ring/cryptoKeys/convqa-scorecard-cmek"
    }
  }

  variables {
    project_id                  = "fictional-convqa-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-convqa-sg/convqa/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "qa-scorecard.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    alert_notification_channels = ["projects/fictional-convqa-sg/notificationChannels/123"]
    iap_members                 = ["group:qa-reviewers@example.com"]
    iap_audience                = "/projects/123456789012/global/backendServices/1234567890123456789"
  }

  assert {
    condition     = google_cloud_run_v2_service.api[0].ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "The API must reject direct public Cloud Run ingress; the load balancer is the only way in."
  }

  assert {
    condition     = google_cloud_run_v2_service.api[0].location == var.region
    error_message = "The serving revision must run in the deployment region."
  }

  assert {
    condition     = endswith(google_cloud_run_v2_service.api[0].template[0].containers[0].image, "@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    error_message = "The API image must remain the reviewed digest."
  }

  assert {
    condition     = google_cloud_run_v2_service.api[0].template[0].encryption_key == google_kms_crypto_key.scorecard.id
    error_message = "The revision must be bound to the regional CMEK: encryption does not cascade."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONVQA_PROFILE"]) == "gcp"
    error_message = "The cloud profile must be named explicitly on the service: an unset profile is not a usable production posture."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "GCP_REGION"]) == var.region
    error_message = "The application region must equal the Terraform deployment region."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "HRZ_HUMAN_REVIEW_URL"]) == var.human_review_url
    error_message = "Rule R8: the service must be told where an escalation is routed, or the managed router refuses."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONVQA_IAP_AUDIENCE"]) == var.iap_audience
    error_message = "The verified identity adapter must receive the exact audience it checks assertions against."
  }

  assert {
    condition     = length([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.name if item.name == "CONVQA_QUALITY_URL"]) == 0
    error_message = "An unset quality URL must be ABSENT from the service, never set to empty: this service reads its environment in three states and an emptied value refuses."
  }

  assert {
    condition     = length(google_compute_backend_service.api[0].iap) == 1 && google_compute_backend_service.api[0].iap[0].enabled
    error_message = "The backend service must carry IAP: it is the only mechanism by which an end user can be authenticated here."
  }

  assert {
    condition     = length(google_iap_web_backend_service_iam_member.reviewers) == 1
    error_message = "The named reviewers must be granted IAP access, or the edge admits nobody."
  }

  assert {
    condition     = length(google_compute_security_policy.api_per_source) == 1
    error_message = "The edge must provision its per-source Cloud Armor abuse boundary."
  }

  assert {
    condition     = one([for rule in google_compute_security_policy.api_per_source[0].rule : rule.rate_limit_options[0].rate_limit_threshold[0].count if rule.action == "throttle"]) == 120
    error_message = "The per-source throttle must retain the reviewed requests-per-minute ceiling."
  }

  assert {
    condition     = google_compute_global_forwarding_rule.edge[0].port_range == "443"
    error_message = "The edge must listen on 443 only: there is no plaintext listener to redirect from."
  }
}

run "reject_region_outside_the_residency_allowlist" {
  command = plan

  variables {
    project_id    = "fictional-convqa-sg"
    enable_vpc_sc = false
    region        = "us-central1"
  }

  expect_failures = [var.region]
}

run "reject_retention_below_six_months" {
  command = plan

  variables {
    project_id     = "fictional-convqa-sg"
    enable_vpc_sc  = false
    retention_days = 179
  }

  expect_failures = [var.retention_days]
}

run "reject_reducing_existing_locked_retention" {
  command = plan

  variables {
    project_id                     = "fictional-convqa-sg"
    enable_vpc_sc                  = false
    retention_days                 = 180
    existing_locked_retention_days = 2557
  }

  expect_failures = [var.existing_locked_retention_days]
}

run "reject_perimeter_without_an_access_policy" {
  command = plan

  variables {
    project_id       = "fictional-convqa-sg"
    enable_vpc_sc    = true
    access_policy_id = ""
  }

  expect_failures = [var.access_policy_id]
}

run "reject_mutable_api_image" {
  command = plan

  variables {
    project_id                  = "fictional-convqa-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-convqa-sg/convqa/api:latest"
    service_domain              = "qa-scorecard.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    alert_notification_channels = ["projects/fictional-convqa-sg/notificationChannels/123"]
  }

  expect_failures = [var.api_image]
}

run "reject_edge_with_no_review_console" {
  command = plan

  variables {
    project_id                  = "fictional-convqa-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-convqa-sg/convqa/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "qa-scorecard.fictional-bank.example"
    human_review_url            = ""
    alert_notification_channels = ["projects/fictional-convqa-sg/notificationChannels/123"]
  }

  expect_failures = [var.human_review_url]
}

run "reject_edge_with_no_alert_channel" {
  command = plan

  variables {
    project_id                  = "fictional-convqa-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-convqa-sg/convqa/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "qa-scorecard.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    alert_notification_channels = []
  }

  expect_failures = [var.alert_notification_channels]
}

run "reject_audience_without_iap" {
  command = plan

  variables {
    project_id       = "fictional-convqa-sg"
    enable_vpc_sc    = false
    edge_iap_enabled = false
    iap_audience     = "/projects/123456789012/global/backendServices/1234567890123456789"
  }

  expect_failures = [terraform_data.edge_contract]
}

run "reject_reserved_secret_override" {
  command = plan

  variables {
    project_id    = "fictional-convqa-sg"
    enable_vpc_sc = false
    additional_secret_env = {
      GCP_REGION = {
        secret_id = "wrong-region"
        version   = "1"
      }
    }
  }

  expect_failures = [var.additional_secret_env]
}

run "reject_moving_secret_version" {
  command = plan

  variables {
    project_id    = "fictional-convqa-sg"
    enable_vpc_sc = false
    additional_secret_env = {
      HRZ7_S2S_TOKEN = {
        secret_id = "hrz7-outbound-s2s"
        version   = "latest"
      }
    }
  }

  expect_failures = [var.additional_secret_env]
}
