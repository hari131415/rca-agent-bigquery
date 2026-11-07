# Remote state: Terraform state lives in a GCS bucket, not on the
# machine that runs Terraform. Cloud Shell's home directory persists,
# but it is a 5 GB disk on a VM that can be recycled, and the state
# file is the only record of what Terraform created — losing it means
# Terraform can no longer manage (or destroy) the resources.
#
# Create the bucket BEFORE terraform init (init reads this block):
#   gsutil mb -p <project-id> -l US gs://<project-id>-tfstate
# The bucket name must be globally unique; if it is taken, pick
# another name and update it here. The bucket holds only the state
# file (a few KB); it costs effectively nothing.
terraform {
  backend "gcs" {
    bucket = "rca-agent-portfolio-tfstate"
    prefix = "terraform/state"
  }
}
