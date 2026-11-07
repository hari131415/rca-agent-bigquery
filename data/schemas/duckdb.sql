-- DuckDB DDL for the offline warehouse. Same tables and columns as
-- data/schemas/bigquery.sql, in DuckDB types. No partitioning: the
-- offline warehouse is a bounded sample, not a cost problem.

CREATE TABLE IF NOT EXISTS complaints (
  cmplid VARCHAR,
  odino VARCHAR,
  mfr_name VARCHAR,
  make VARCHAR,
  model VARCHAR,
  model_year INTEGER,
  crash BOOLEAN,
  faildate DATE,
  fire BOOLEAN,
  injured INTEGER,
  deaths INTEGER,
  compdesc VARCHAR,
  state VARCHAR,
  datea DATE,
  ldate DATE,
  miles INTEGER,
  occurrences INTEGER,
  cdescr VARCHAR,
  cmpl_type VARCHAR,
  prod_type VARCHAR,
  medical_attn BOOLEAN,
  vehicles_towed_yn BOOLEAN,
  state_of_incident VARCHAR,
  is_synthetic BOOLEAN NOT NULL,
  plant_id VARCHAR,
  line_id VARCHAR,
  supplier_id VARCHAR
);

CREATE TABLE IF NOT EXISTS recalls (
  record_id INTEGER,
  campno VARCHAR,
  make VARCHAR,
  model VARCHAR,
  model_year INTEGER,
  mfgcampno VARCHAR,
  compname VARCHAR,
  mfgname VARCHAR,
  bgman DATE,
  endman DATE,
  rcltypecd VARCHAR,
  potaff INTEGER,
  odate DATE,
  influenced_by VARCHAR,
  mfgtxt VARCHAR,
  rcdate DATE,
  datea DATE,
  rpno VARCHAR,
  fmvss VARCHAR,
  desc_defect VARCHAR,
  conequence_defect VARCHAR,
  corrective_action VARCHAR,
  notes VARCHAR,
  rcl_cmpt_id VARCHAR,
  mfr_comp_name VARCHAR,
  mfr_comp_desc VARCHAR,
  mfr_comp_ptno VARCHAR,
  do_not_drive VARCHAR,
  park_outside VARCHAR
);

CREATE TABLE IF NOT EXISTS plants (
  plant_id VARCHAR NOT NULL,
  name VARCHAR,
  state VARCHAR
);

CREATE TABLE IF NOT EXISTS lines (
  line_id VARCHAR NOT NULL,
  plant_id VARCHAR NOT NULL,
  product VARCHAR
);

CREATE TABLE IF NOT EXISTS suppliers (
  supplier_id VARCHAR NOT NULL,
  name VARCHAR,
  part VARCHAR
);

CREATE TABLE IF NOT EXISTS builds (
  build_id VARCHAR NOT NULL,
  make VARCHAR,
  model VARCHAR,
  model_year INTEGER,
  plant_id VARCHAR,
  line_id VARCHAR,
  build_date DATE,
  supplier_brake VARCHAR,
  supplier_steering VARCHAR,
  supplier_airbag VARCHAR,
  vehicles_built INTEGER
);
