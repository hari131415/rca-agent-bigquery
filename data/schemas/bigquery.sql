-- BigQuery DDL for the rca_agent dataset. Source of truth for the
-- Terraform tables and the loader. The dataset name `rca_agent` is
-- replaced with the configured dataset by load_bigquery.py.
-- Partitioning: complaints is partitioned by received date (ldate)
-- and clustered by make/model_year, the filters nearly every
-- investigation uses. Run with: bq query --use_legacy_sql=false < bigquery.sql

CREATE TABLE IF NOT EXISTS `rca_agent.complaints` (
  cmplid STRING,
  odino STRING,
  mfr_name STRING,
  make STRING,
  model STRING,
  model_year INT64,
  crash BOOL,
  faildate DATE,
  fire BOOL,
  injured INT64,
  deaths INT64,
  compdesc STRING,
  state STRING,
  datea DATE,
  ldate DATE,
  miles INT64,
  occurrences INT64,
  cdescr STRING,
  cmpl_type STRING,
  prod_type STRING,
  medical_attn BOOL,
  vehicles_towed_yn BOOL,
  state_of_incident STRING,
  is_synthetic BOOL NOT NULL,
  plant_id STRING,
  line_id STRING,
  supplier_id STRING
)
PARTITION BY ldate
CLUSTER BY make, model_year;

CREATE TABLE IF NOT EXISTS `rca_agent.recalls` (
  record_id INT64,
  campno STRING,
  make STRING,
  model STRING,
  model_year INT64,
  mfgcampno STRING,
  compname STRING,
  mfgname STRING,
  bgman DATE,
  endman DATE,
  rcltypecd STRING,
  potaff INT64,
  odate DATE,
  influenced_by STRING,
  mfgtxt STRING,
  rcdate DATE,
  datea DATE,
  rpno STRING,
  fmvss STRING,
  desc_defect STRING,
  conequence_defect STRING,
  corrective_action STRING,
  notes STRING,
  rcl_cmpt_id STRING,
  mfr_comp_name STRING,
  mfr_comp_desc STRING,
  mfr_comp_ptno STRING,
  do_not_drive STRING,
  park_outside STRING
);

CREATE TABLE IF NOT EXISTS `rca_agent.plants` (
  plant_id STRING NOT NULL,
  name STRING,
  state STRING
);

CREATE TABLE IF NOT EXISTS `rca_agent.lines` (
  line_id STRING NOT NULL,
  plant_id STRING NOT NULL,
  product STRING
);

CREATE TABLE IF NOT EXISTS `rca_agent.suppliers` (
  supplier_id STRING NOT NULL,
  name STRING,
  part STRING
);

CREATE TABLE IF NOT EXISTS `rca_agent.builds` (
  build_id STRING NOT NULL,
  make STRING,
  model STRING,
  model_year INT64,
  plant_id STRING,
  line_id STRING,
  build_date DATE,
  supplier_brake STRING,
  supplier_steering STRING,
  supplier_airbag STRING,
  vehicles_built INT64
);
