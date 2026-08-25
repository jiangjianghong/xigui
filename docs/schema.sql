-- AI交叉校验 表结构参考
--
-- 应用启动时会自动建库建表(见 app/db/bootstrap.py),本文件用于:
--   1. 应用账号无 CREATE 权限时,交给 DBA 手工执行
--   2. 后续表结构变更的对照基准——本项目不做字段自动迁移,
--      改表结构需人工执行 ALTER 并同步更新本文件与 app/db/models.py
--
-- 字符集固定 utf8mb4:规则里全是中文 prompt。

CREATE DATABASE IF NOT EXISTS `ai_cross_check`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `ai_cross_check`;

CREATE TABLE IF NOT EXISTS `projects` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `key`         VARCHAR(64)  NOT NULL COMMENT '稳定标识,yaml 导入导出引用它',
  `name`        VARCHAR(128) NOT NULL,
  `description` VARCHAR(512) NOT NULL DEFAULT '',
  `sort_order`  INT          NOT NULL DEFAULT 0,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_projects_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `rules` (
  `id`                 BIGINT       NOT NULL AUTO_INCREMENT,
  `rule_id`            VARCHAR(64)  NOT NULL COMMENT '业务 ID,下游 /compare 传的就是它',
  `project_id`         BIGINT       NULL COMMENT 'NULL = 未分组',
  `name`               VARCHAR(128) NOT NULL DEFAULT '',
  `description`        VARCHAR(512) NOT NULL DEFAULT '',
  `field_defs`         JSON         NULL COMMENT 'list[str];NULL 表示用默认 field_a/field_b',
  `require_field_name` TINYINT(1)   NOT NULL DEFAULT 0,
  `system_prompt`      TEXT         NULL,
  `prompt_template`    TEXT         NOT NULL,
  `sort_order`         INT          NOT NULL DEFAULT 0,
  `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rules_rule_id` (`rule_id`),
  KEY `idx_rules_project` (`project_id`),
  CONSTRAINT `fk_rules_project` FOREIGN KEY (`project_id`)
    REFERENCES `projects` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 刻意不设外键:存的是调用当时的快照。规则后续改名/删除/换项目
-- 都不该影响既有历史。
CREATE TABLE IF NOT EXISTS `compare_history` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `rule_id`      VARCHAR(64)  NOT NULL,
  `rule_name`    VARCHAR(128) NOT NULL DEFAULT '',
  `project_key`  VARCHAR(64)  NULL,
  `project_name` VARCHAR(128) NULL,
  `fields`       JSON         NOT NULL,
  `result`       TINYINT(1)   NOT NULL,
  `reason`       TEXT         NULL,
  `time_ms`      INT          NOT NULL,
  `cost`         JSON         NULL,
  `raw_request`  JSON         NULL,
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_hist_rule_created` (`rule_id`, `created_at`),
  KEY `idx_hist_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
