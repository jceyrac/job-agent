# Quickstart: Monitored Companies Filter Validation

**Feature**: 012-monitored-companies-filter-in-tracker

## Prerequisites

- Running tracker: `streamlit run tracker.py`
- DB with both monitored and non-monitored jobs (any state)

## Validation scenarios

### 1. Default behavior — "All" selected

1. Open Jobs tab
2. Confirm "Source type" selectbox appears in sidebar, below Source multiselect, under a divider
3. Default value is "All"
4. Confirm total job count matches expected (no filtering applied)

### 2. "Monitored companies" filter

1. Select "Monitored companies" from "Source type"
2. Confirm all visible jobs have a company with monitored status (check company detail page)
3. Pick a random job → open its detail → confirm `monitored_company_id` is populated

### 3. "Job boards only" filter

1. Select "Job boards only" from "Source type"
2. Confirm all visible jobs show "Source: [board name]" (LinkedIn, Indeed, Wellfound, etc.)
3. Pick a random job → open its detail → confirm `monitored_company_id` is NULL (not shown)

### 4. Composition with other filters

1. "Source type" = "Monitored companies" + Source = "greenhouse"
2. Only Greenhouse-sourced monitored-company jobs should appear
3. "Source type" = "Job boards only" + Score filter
4. Score filter should work normally on the filtered subset

### 5. Regression check

1. Reset all filters ("All" for Source type)
2. Navigate Dashboard → confirm DB stats unchanged
3. Navigate Companies → confirm company list unchanged
4. Navigate Settings → confirm no regressions
