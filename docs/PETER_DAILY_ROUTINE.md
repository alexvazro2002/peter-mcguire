# Peter's daily routine

At launch, Peter writes and speaks “Good morning, sir. Ready for today?” before noon, “Good afternoon, sir. Ready for today?” from noon until 18:00, and the equivalent evening greeting thereafter. He uses the configured Peter timezone, Europe/London by default, including daylight saving changes. The greeting switch and speech mute remain available in Settings.

A direct positive reply such as “Yes, please”, “I'm ready”, or “Let's begin” starts a local calendar briefing immediately. Peter lists today's calendar items in time order, unfinished tasks scheduled for today, and older overdue tasks separately. Completed, future and unscheduled tasks are not described as pending today. He leaves the calendar unchanged. Declining postpones the briefing; a different request proceeds normally and ends the readiness question's context.

When Gmail is connected, startup checks queue new inbox headers without fetching or speaking message bodies. After the calendar speech finishes, Peter offers one message at a time: “You have a new email from X, would you like me to read it for you, sir?” A direct positive reply reads that message; “No” or “Skip it” leaves it and advances to the next offer. Unrelated instructions end the active consent question. Queued headers are account-bound and saved privately under `.runtime`; stale consent does not survive a restart. Body readings are bounded and explicitly identify partial readings. Attachments are not read. Gmail does not mark messages read or modify them.

Google sign-in setup remains a separate step. Until Peter has its own read-only Gmail connection, the calendar routine works and email offers stay inactive.

Peter uses George's natural British male voice at normal pace (`voice_speed=1.0`, `voice_pitch=0.0`). The earlier artificial pitch lowering is removed. Speech and transcription remain local, and reasoning continues to use the existing LM Studio model.

The greeting, your reply, the calendar briefing, and each email offer/read appear in Home. Messages retain the existing fade after twenty idle seconds and Show history/Hide controls.
