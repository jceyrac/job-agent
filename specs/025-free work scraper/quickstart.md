# Quickstart — Free-Work.com Scraper

**Feature**: Spec 025  
**Date**: 2026-08-06

---

## Prerequisites

- Python 3.11 venv activé (`.venv/`)
- Dépendances déjà installées (`requests`, `beautifulsoup4`)
- Pas de clé API, pas d'auth

---

## Validation rapide (manuel)

### 1. Test unitaire du mapping

```bash
python3 -c "
from scrapers.boards.free_work import FreeWorkScraper
from models import JobFilter

scraper = FreeWorkScraper(storage=None)
# Test sur un seul slug pour aller vite
scraper.FREE_WORK_SLUGS = ['product-owner']  # override temporaire
jobs = scraper.fetch(JobFilter(titles=['product owner']))
print(f'Fetched: {len(jobs)} jobs')
for j in jobs[:3]:
    print(f'  {j.title[:60]}')
    print(f'    work_mode={j.work_mode} contract_type={j.contract_type} salary={j.salary}')
    print(f'    location={j.location} country_code={j.country_code}')
    print(f'    url={j.url}')
    print()
"
```

**Expected**: 20-100 jobs, chaque job a `work_mode`, `contract_type`, `location`, `url` renseignés.

### 2. Vérification des mappings

```bash
python3 -c "
from scrapers.boards.free_work import FreeWorkScraper
from models import JobFilter

scraper = FreeWorkScraper(storage=None)
scraper.FREE_WORK_SLUGS = ['product-owner', 'scrum-master']
jobs = scraper.fetch(JobFilter(titles=['product owner', 'scrum master']))

# Vérifie que les champs critiques sont renseignés
miss = {'work_mode': 0, 'contract_type': 0, 'url': 0, 'description': 0, 'salary': 0}
for j in jobs:
    if not j.work_mode: miss['work_mode'] += 1
    if not j.contract_type: miss['contract_type'] += 1
    if not j.url: miss['url'] += 1
    if not j.description: miss['description'] += 1
    if not j.salary: miss['salary'] += 1

total = len(jobs)
for field, missing in miss.items():
    pct = 100 * missing / total if total else 0
    print(f'{field}: {total - missing}/{total} filled ({pct:.0f}% missing)')

# Vérifie la dédup (pas de doublons d'id)
seen = set()
dupes = 0
for j in jobs:
    # Extraire l'id de l'URL
    import re
    m = re.search(r'/job-mission/[^/]+/([^/]+)', j.url)
    if m:
        slug = m.group(1)
        if slug in seen: dupes += 1
        seen.add(slug)
print(f'Duplicates: {dupes}')
"
```

### 3. Test d'intégration via scrape.py

```bash
# Test découverte
python3 -c "
from scrape import discover_scrapers
scrapers = discover_scrapers()
names = [s.SOURCE_NAME for s in scrapers]
print('free_work' in names, names)
"
```

**Expected**: `True` (free_work présent dans la liste des scrapers découverts).

### 4. Vérification régression FELFEL

```bash
# Le scoring existant ne doit pas être affecté
python score.py --profile <profile_id> 2>&1 | grep -i "felfel"
```

**Expected**: FELFEL toujours scoré correctement (inchangé).

---

## Run complet

```bash
# Scrape complet (tous les scrapers, dont free_work)
python scrape.py
```

Le scraper Free-Work va :
1. Itérer sur les 11 slugs (1 req/s)
2. Pour chaque slug, paginer si >100 résultats
3. Dédupliquer par `id` API
4. Retourner ~563 `JobPosting`

Temps estimé : ~20-25 secondes.

---

## Debug

```bash
# Activer le logging debug
python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)

from scrapers.boards.free_work import FreeWorkScraper
from models import JobFilter

scraper = FreeWorkScraper(storage=None)
jobs = scraper.fetch(JobFilter(titles=['product owner']))
print(f'{len(jobs)} jobs fetched')
"
```
