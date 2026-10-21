-- Migração 004: correções da revisão da v2.
-- revisit_traction: tração medida na última revisita (base para promover quem subiu).
-- promoted_at: quando foi promovida (o bônus na fila sobrevive ao status virar 'queued').
-- kit_at: quando o kit foi gerado (trava do kit da semana não depende da data do dossiê).

ALTER TABLE companies ADD COLUMN revisit_traction REAL;
ALTER TABLE companies ADD COLUMN promoted_at TEXT;
ALTER TABLE dossiers ADD COLUMN kit_at TEXT;
UPDATE dossiers SET kit_at = created_at WHERE kit IS NOT NULL;
UPDATE companies SET promoted_at = status_changed_at WHERE status = 'promoted';
