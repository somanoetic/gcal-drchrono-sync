/**
 * Google Apps Script: Calendar Change → GitHub Actions Trigger
 *
 * SETUP:
 * 1. Go to https://script.google.com → New Project
 * 2. Paste this entire file
 * 3. Go to Project Settings (gear icon) → Script Properties → Add:
 *      Property: GITHUB_TOKEN
 *      Value: (your fine-grained GitHub PAT with Contents: Read/Write on gcal-drchrono-sync)
 * 4. Go to Triggers (clock icon) → Add Trigger:
 *      Function: onCalendarChange
 *      Event source: From calendar
 *      Calendar owner email: hadfield.neil@gmail.com
 *      Event type: On event updated
 * 5. Authorize when prompted
 *
 * The trigger fires instantly when any calendar event is created, updated, or deleted.
 * A 60-second debounce prevents rapid-fire dispatches.
 */

var GITHUB_REPO = 'somanoetic/gcal-drchrono-sync';

function onCalendarChange(e) {
  var token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    Logger.log('ERROR: GITHUB_TOKEN not set in Script Properties');
    return;
  }

  // Debounce: skip if we dispatched in the last 60 seconds
  var cache = CacheService.getScriptCache();
  if (cache.get('last_dispatch')) {
    Logger.log('Debounced — skipping (last dispatch < 60s ago)');
    return;
  }
  cache.put('last_dispatch', new Date().toISOString(), 60);

  // Trigger GitHub Actions via repository_dispatch
  var url = 'https://api.github.com/repos/' + GITHUB_REPO + '/dispatches';
  var options = {
    method: 'post',
    headers: {
      'Authorization': 'Bearer ' + token,
      'Accept': 'application/vnd.github.v3+json',
    },
    contentType: 'application/json',
    payload: JSON.stringify({
      event_type: 'calendar-changed',
      client_payload: {
        calendar_id: e ? (e.calendarId || 'unknown') : 'manual',
        triggered_at: new Date().toISOString(),
      }
    }),
    muteHttpExceptions: true,
  };

  var response = UrlFetchApp.fetch(url, options);
  var code = response.getResponseCode();
  if (code === 204) {
    Logger.log('GitHub Actions dispatched successfully');
  } else {
    Logger.log('GitHub dispatch failed: ' + code + ' ' + response.getContentText());
  }
}

// Manual test function — run this from the Apps Script editor to verify setup
function testDispatch() {
  onCalendarChange(null);
}
