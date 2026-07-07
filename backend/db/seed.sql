-- Dev seed. Do NOT run in production.
INSERT INTO clients (slug, name, industry, region, data_tier)
VALUES ('demo', 'Demo Company', 'hospitality', 'eu', 'standard')
ON CONFLICT (slug) DO NOTHING;
INSERT INTO scoring_configs (client_id, version, dimensions, weights, rubric, is_active)
SELECT c.id, 1,
  '[{"key":"politeness","label":"Politeness"},{"key":"correctness","label":"Correctness"},{"key":"problem_solving","label":"Problem solving"},{"key":"time_efficiency","label":"Time efficiency"}]'::jsonb,
  '{"politeness":1,"correctness":3,"problem_solving":2,"time_efficiency":1}'::jsonb,
  'Score each dimension 0-100 using the client knowledge base.', true
FROM clients c WHERE c.slug = 'demo'
ON CONFLICT (client_id, version) DO NOTHING;
