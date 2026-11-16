-- Upgrade an existing MySQL database for judge/custom rules.
-- Manual fallback/reference for the startup migration in app/db/migrations.py.
-- Do not run this after the service has already completed the migration.

ALTER TABLE `rules`
  ADD COLUMN `rule_type` VARCHAR(16) NOT NULL DEFAULT 'judge' AFTER `description`,
  ADD COLUMN `output_schema` JSON NULL AFTER `prompt_template`;

ALTER TABLE `compare_history`
  ADD COLUMN `rule_type` VARCHAR(16) NOT NULL DEFAULT 'judge' AFTER `rule_name`,
  ADD COLUMN `result_json` JSON NULL AFTER `fields`;

UPDATE `compare_history`
SET `result_json` = JSON_EXTRACT(IF(`result` <> 0, 'true', 'false'), '$');

ALTER TABLE `compare_history`
  DROP COLUMN `result`,
  CHANGE COLUMN `result_json` `result` JSON NOT NULL;
