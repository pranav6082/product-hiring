-- Sources: one row per data source type
CREATE TABLE sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  scraper_class TEXT,
  is_active BOOLEAN DEFAULT true,
  config JSONB
);

INSERT INTO sources (name, scraper_class, is_active) VALUES
  ('linkedin_feed', 'LinkedInFeedScraper', false),
  ('linkedin_jobs', 'LinkedInJobsScraper', false),
  ('telegram', 'TelegramScraper', false),
  ('whatsapp', 'WhatsAppScraper', false),
  ('manual', NULL, true);

-- People: the network anchor (who posted or shared)
CREATE TABLE people (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  linkedin_url TEXT UNIQUE,
  linkedin_id TEXT UNIQUE,
  telegram_handle TEXT UNIQUE,
  whatsapp_number TEXT,
  known_to_pranav BOOLEAN DEFAULT false,
  relationship_strength INTEGER DEFAULT 0 CHECK (relationship_strength BETWEEN 0 AND 3),
  notes TEXT,
  first_seen_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Jobs: canonical job record, deduplicated across all sources
CREATE TABLE jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT,
  company TEXT,
  location TEXT,
  employment_type TEXT,
  domain TEXT CHECK (domain IN ('pm', 'design', 'data', 'other')),
  raw_title TEXT,
  raw_company TEXT,
  raw_location TEXT,
  raw_employment_type TEXT,
  norm_title TEXT,
  norm_company TEXT,
  norm_location_city TEXT,
  norm_location_region TEXT,
  norm_location_country TEXT,
  norm_remote_type TEXT,
  norm_seniority TEXT,
  norm_function TEXT,
  normalization_confidence NUMERIC(4,3) DEFAULT 0,
  needs_review BOOLEAN DEFAULT true,
  review_status TEXT DEFAULT 'pending' CHECK (review_status IN ('pending', 'approved', 'rejected')),
  reviewed_at TIMESTAMPTZ,
  reviewed_by TEXT,
  review_notes TEXT,
  job_url TEXT UNIQUE,
  description_summary TEXT,
  first_seen_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  is_active BOOLEAN DEFAULT true
);

-- Signals: every sighting from every source
CREATE TABLE signals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
  source_id UUID NOT NULL REFERENCES sources(id),
  person_id UUID REFERENCES people(id) ON DELETE SET NULL,
  signal_url TEXT,
  profile_url TEXT,
  raw_text TEXT NOT NULL,
  urgency_signals TEXT[],
  post_date TIMESTAMPTZ,
  scraped_at TIMESTAMPTZ DEFAULT now(),
  validated BOOLEAN DEFAULT false,
  validation_result JSONB,
  validated_at TIMESTAMPTZ,
  briefed BOOLEAN DEFAULT false,
  briefed_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX idx_signals_job_id ON signals(job_id);
CREATE INDEX idx_signals_source_id ON signals(source_id);
CREATE INDEX idx_signals_person_id ON signals(person_id);
CREATE INDEX idx_signals_scraped_at ON signals(scraped_at DESC);
CREATE INDEX idx_signals_unbriefed ON signals(briefed) WHERE briefed = false;
CREATE INDEX idx_jobs_domain ON jobs(domain);
CREATE INDEX idx_jobs_company ON jobs(company);
CREATE INDEX idx_jobs_active ON jobs(is_active) WHERE is_active = true;
CREATE INDEX idx_jobs_review_status ON jobs(review_status);
CREATE INDEX idx_jobs_needs_review ON jobs(needs_review) WHERE needs_review = true;
CREATE INDEX idx_people_known ON people(known_to_pranav) WHERE known_to_pranav = true;
