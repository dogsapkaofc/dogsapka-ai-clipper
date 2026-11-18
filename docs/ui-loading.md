# Navigation and loading

Page navigation animates a flat snapshot of the outgoing page over the live next
page. It does not move layout-managed pages or apply opacity to their nested
shadow/video widgets. Repeated clicks on the selected tab are a no-op; rapid
switches, resize and hiding clean up the transition. Restoring from the tray or
taskbar preserves the current page, scroll position and selection. Whole-window
fade-in runs only on the first opening.

Library queries, export checks and thumbnail decoding run in a worker. Only one
query is active at a time; newer filter requests supersede its result. Large
lists build in small GUI batches. Clean revisits and unchanged results keep the
existing cards; small updates replace only changed cards. Loading and errors are
shown inline. Worker threads create QImage data, while QPixmap/widgets remain on
the GUI thread.

Streamer monitoring changes run in the background with duplicate-request guards
and inline status. Unchanged streamer cards survive refreshes. Removed controls
stay parented and hidden until deferred deletion instead of briefly becoming
top-level windows. Empty-state widgets are parented before showing.

Editor previews and waveforms each allow one active task plus the latest pending
request. Each preview owns its temporary ASS/image files. Stale and destroyed
page callbacks are ignored. Manual uploads also reject duplicate submissions
while running and safely handle a closed dialog. Existing confirmations for
publishing, removal, account connection and stopping recordings are retained.

Regression tests cover transition midpoint painting, rapid switching, restore
state, widget ownership, large-list event-loop heartbeats, cached card reuse,
asynchronous monitoring, duplicate uploads and deleted-widget callbacks. Normal
processing does not introduce a new generic progress dialog. These tests do not
replace verification of every possible Windows desktop/compositor condition.
