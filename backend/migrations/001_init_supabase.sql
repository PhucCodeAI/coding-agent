-- Backend/migrations/001_init_supabase.sql
--
-- Khởi tạo toàn bộ schema Supabase cho Backend:
--   1. public.traces            — metadata trace (TraceRepository)
--   2. public.sessions          — phiên chat (SessionRepository)
--   3. public.session_messages  — lịch sử tin nhắn + SSE events của phiên
--   4. storage bucket 'trace-logs' — blob JSON chi tiết spans (StorageRepository)
--
-- Cách chạy: Supabase Dashboard → SQL Editor → New query → dán toàn bộ file → Run.
-- Idempotent: chạy lại nhiều lần không lỗi, không mất dữ liệu.
--
-- Tên bảng/bucket phải khớp Backend/config/config.toml:
--   [database] table_name, sessions_table, messages_table
--   [storage]  bucket

-- ============================================================
-- 1. Traces
-- ============================================================
create table if not exists public.traces (
  trace_id          text primary key,
  session_id        text not null,
  prompt            text not null,
  status            text not null,
  duration_ms       integer not null default 0,
  total_tokens      integer not null default 0,
  input_tokens      integer not null default 0,
  output_tokens     integer not null default 0,
  reasoning_tokens  integer not null default 0,
  cache_tokens      integer not null default 0,
  cost              double precision not null default 0,
  storage_path      text,                      -- <session_id>/<trace_id>.json trong bucket
  error_message     text,                      -- dạng "[CODE] chi tiết"
  created_at        timestamptz not null default now(),
  finished_at       timestamptz
);

create index if not exists traces_created_at_idx
  on public.traces (created_at desc);
create index if not exists traces_session_created_idx
  on public.traces (session_id, created_at desc);
create index if not exists traces_status_created_idx
  on public.traces (status, created_at desc);

-- ============================================================
-- 2. Sessions
-- ============================================================
create table if not exists public.sessions (
  session_id     text primary key,
  title          text not null default 'Phiên mới',
  last_status    text,
  message_count  integer not null default 0,
  total_tokens   integer not null default 0,
  cost           double precision not null default 0,
  last_trace_id  text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint sessions_id_format check (session_id ~ '^[A-Za-z0-9_-]{1,64}$')
);

create index if not exists sessions_updated_at_idx
  on public.sessions (updated_at desc);

-- ============================================================
-- 3. Session messages
-- ============================================================
create table if not exists public.session_messages (
  message_id   text primary key,
  session_id   text not null
               references public.sessions (session_id) on delete cascade,
  role         text not null check (role in ('user', 'assistant')),
  content      text not null default '',       -- user: prompt | assistant: tóm tắt kết quả
  status       text,                           -- assistant: SUCCESS | FAILED | CANCELLED
  trace_id     text,
  duration_ms  integer,
  events       jsonb not null default '[]'::jsonb,  -- assistant: SSE events để UI replay
  created_at   timestamptz not null default now()
);

create index if not exists session_messages_session_created_idx
  on public.session_messages (session_id, created_at);

-- ============================================================
-- 4. Storage bucket cho blob trace (private)
-- ============================================================
insert into storage.buckets (id, name, public)
values ('trace-logs', 'trace-logs', false)
on conflict (id) do nothing;

-- ============================================================
-- 5. Quyền truy cập
--
-- KHUYẾN NGHỊ: Backend dùng SERVICE ROLE KEY (Project Settings → API →
-- service_role) cho SUPABASE_KEY. Service role bỏ qua RLS → không cần phần
-- policy bên dưới. KHÔNG BAO GIỜ đưa service role key xuống UI.
--
-- Nếu buộc phải dùng ANON KEY cho Backend (chỉ nên ở môi trường dev), bỏ
-- comment khối dưới để cấp quyền cho role anon. Cảnh báo: ai có anon key
-- (key này public) đều đọc/ghi/xóa được các bảng này.
-- ============================================================

-- -- Bảng: bật RLS + cho anon toàn quyền
-- alter table public.traces           enable row level security;
-- alter table public.sessions         enable row level security;
-- alter table public.session_messages enable row level security;
--
-- drop policy if exists "backend_all_traces" on public.traces;
-- create policy "backend_all_traces" on public.traces
--   for all to anon using (true) with check (true);
--
-- drop policy if exists "backend_all_sessions" on public.sessions;
-- create policy "backend_all_sessions" on public.sessions
--   for all to anon using (true) with check (true);
--
-- drop policy if exists "backend_all_session_messages" on public.session_messages;
-- create policy "backend_all_session_messages" on public.session_messages
--   for all to anon using (true) with check (true);
--
-- -- Storage: cho anon đọc/ghi/xóa object trong bucket trace-logs
-- drop policy if exists "backend_trace_logs_all" on storage.objects;
-- create policy "backend_trace_logs_all" on storage.objects
--   for all to anon
--   using (bucket_id = 'trace-logs')
--   with check (bucket_id = 'trace-logs');

-- ============================================================
-- Kiểm tra nhanh sau khi chạy
-- ============================================================
-- select table_name from information_schema.tables
--   where table_schema = 'public'
--     and table_name in ('traces', 'sessions', 'session_messages');
-- select id, public from storage.buckets where id = 'trace-logs';
