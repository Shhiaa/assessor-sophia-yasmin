-- Esquema mínimo para testar localmente (inferido das queries das tools).
-- Uso: docker run -d --name pg-teste -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=teste -p 55432:5432 postgres:16
--      docker exec -i pg-teste psql -U postgres -d teste < testes/schema_local.sql
--      DATABASE_URL=postgres://postgres:teste@localhost:55432/teste python testes/prova_depois.py

CREATE TABLE transaction_types (id serial PRIMARY KEY, type text NOT NULL);
INSERT INTO transaction_types (id, type) VALUES (1, 'INCOME'), (2, 'EXPENSES'), (3, 'TRANSFER');

CREATE TABLE categories (id serial PRIMARY KEY, name text NOT NULL);
INSERT INTO categories (id, name) VALUES
  (1, 'ALIMENTAÇÃO'), (2, 'TRANSPORTE'), (3, 'MORADIA'), (4, 'SAÚDE'), (5, 'LAZER'),
  (6, 'EDUCAÇÃO'), (7, 'SALÁRIO'), (8, 'INVESTIMENTOS'), (9, 'COMPRAS'), (10, 'SERVIÇOS'),
  (11, 'IMPOSTOS'), (12, 'OUTROS');

CREATE TABLE transactions (
  id serial PRIMARY KEY,
  amount numeric(12,2) NOT NULL,
  type int NOT NULL REFERENCES transaction_types(id),
  category_id int NOT NULL REFERENCES categories(id),
  description text,
  payment_method text,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  source_text text
);

CREATE TABLE events (
  id serial PRIMARY KEY,
  title text NOT NULL,
  start_time timestamptz NOT NULL,
  end_time timestamptz,
  location text,
  notes text,
  source_text text,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
