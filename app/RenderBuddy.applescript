-- RenderBuddy launcher: turns the STEVE connector on or off, or removes it. make_app.sh fills in the folder.
-- Opening it shows buttons: Turn on / Turn off, and More… (pair again, show the log, remove RenderBuddy). The first
-- time, it asks for a STEVE pairing code. renderbuddy://start, stop and uninstall do those things straight away.
use framework "AppKit"
use scripting additions

property bbFolder : "__FOLDER__"

on isRunning()
	try
		do shell script "kill -0 $(cat \"$HOME/.renderbuddy/connector.pid\") 2>/dev/null"
		return true
	on error
		return false
	end try
end isRunning

-- Starts the connector in the background. Everything is redirected (stdout, stderr, stdin) and only the one
-- command is backgrounded: otherwise `do shell script` waits for it to finish and the app freezes.
on launchConnector()
	do shell script "cd " & quoted form of bbFolder & " || exit 1; mkdir -p \"$HOME/.renderbuddy\"; nohup .venv/bin/python renderbuddy.py run >> \"$HOME/.renderbuddy/connector.log\" 2>&1 < /dev/null &"
end launchConnector

on isPaired()
	try
		do shell script "cd " & quoted form of bbFolder & " && .venv/bin/python renderbuddy.py paired"
		return true
	on error
		return false
	end try
end isPaired

-- Asks for the code STEVE shows under Settings → Connectors → Pair, enrolls with it, and copies the
-- fingerprint to the clipboard for the person to paste into STEVE. Returns true when it worked.
on pairBB()
	try
		set reply to display dialog "Connect RenderBuddy to your STEVE account." & return & return & "1. In STEVE, open Settings → Connectors → Pair." & return & "2. Copy the pairing code it shows." & return & "3. Paste it here:" default answer "" buttons {"Cancel", "Pair"} default button "Pair" with title "RenderBuddy"
	on error
		return false
	end try
	set theCode to text returned of reply
	if theCode is "" then return false
	try
		set out to do shell script "cd " & quoted form of bbFolder & " && .venv/bin/python renderbuddy.py pair " & quoted form of theCode & " 2>&1"
	on error errMsg
		display dialog "Pairing didn't work:" & return & return & errMsg & return & return & "A code works once and expires after 10 minutes. Get a fresh one in STEVE and try again." buttons {"OK"} default button "OK" with title "RenderBuddy" with icon caution
		return false
	end try
	set fp to do shell script "echo " & quoted form of out & " | grep -o 'SHA256:[A-Za-z0-9+/=_-]*' | head -1"
	set the clipboard to fp
	display dialog "Paired. One last step, in STEVE:" & return & return & "Paste this fingerprint to confirm it (already copied for you):" & return & fp & return & return & "Then allow RenderBuddy's actions. For safety, let agents' jobs wait for your OK." buttons {"OK"} default button "OK" with title "RenderBuddy"
	return true
end pairBB

on startBB()
	set justPaired to false
	if not isPaired() then
		if not pairBB() then return
		set justPaired to true
	end if
	my launchConnector()
	delay 2
	if isRunning() then
		if justPaired then
			display notification "Waiting for you to confirm the fingerprint in STEVE." with title "RenderBuddy is on"
		else
			display notification "Ready for STEVE. It turns itself off after a while with no jobs." with title "RenderBuddy is on"
		end if
	else
		set choice to button returned of (display dialog "RenderBuddy didn't start. The log says why." buttons {"Show log", "OK"} default button "Show log" with title "RenderBuddy" with icon caution)
		if choice is "Show log" then showLog()
	end if
end startBB

on stopBB()
	do shell script "kill $(cat \"$HOME/.renderbuddy/connector.pid\") 2>/dev/null; true"
	display notification "Turned off. Open RenderBuddy again when you need it." with title "RenderBuddy is off"
end stopBB

on showLog()
	do shell script "touch \"$HOME/.renderbuddy/connector.log\"; open -a Console \"$HOME/.renderbuddy/connector.log\""
end showLog

-- Moves RenderBuddy's three pieces to the Trash (so it can be undone until the Trash is emptied).
on uninstallBB()
	set theText to "This moves RenderBuddy to the Trash:" & return & "  • this app" & return & "  • the program folder and its Python setup (" & bbFolder & ")" & return & "  • its settings and render job files (~/.renderbuddy)" & return & return & "What about the renders (pictures and videos) in the program folder?"
	set choice to button returned of (display dialog theText buttons {"Cancel", "Keep my renders", "Trash them too"} default button "Cancel" with title "Remove RenderBuddy" with icon caution)
	if choice is "Cancel" then return
	if isRunning() then do shell script "kill $(cat \"$HOME/.renderbuddy/connector.pid\") 2>/dev/null; sleep 1; true"
	set keptNote to ""
	if choice is "Keep my renders" then
		try
			set kept to do shell script "src=" & quoted form of bbFolder & "/renders; if [ -d \"$src\" ]; then mkdir -p \"$HOME/Movies\"; dest=\"$HOME/Movies/RenderBuddy renders\"; [ -e \"$dest\" ] && dest=\"$dest $(date +%Y%m%d-%H%M%S)\"; mv \"$src\" \"$dest\" && echo \"$dest\"; fi"
			if kept is not "" then set keptNote to return & return & "Your renders are now in: " & kept
		end try
	end if
	set homePath to POSIX path of (path to home folder)
	set myPath to POSIX path of (path to me)
	set targets to {}
	repeat with p in {bbFolder, homePath & ".renderbuddy", myPath}
		set p to p as text
		if p ends with "/" and (length of p) > 1 then set p to text 1 thru -2 of p
		try
			do shell script "test -e " & quoted form of p
			set end of targets to (POSIX file p)
		end try
	end repeat
	tell application "Finder" to delete targets
	display dialog "RenderBuddy is in the Trash. Empty the Trash to finish." & return & return & "One more thing: in STEVE, remove the RenderBuddy connector under Settings → Connectors." & keptNote buttons {"OK"} default button "OK" with title "RenderBuddy removed"
end uninstallBB

-- The More… list. Returns true when the person clicked Back, so the main window can come up again.
on moreChoices()
	set picked to choose from list {"Pair with STEVE again", "Show log", "Remove RenderBuddy…"} with title "RenderBuddy" with prompt "More things RenderBuddy can do:" OK button name "Choose" cancel button name "Back"
	if picked is false then return true
	set picked to item 1 of picked
	if picked is "Show log" then
		showLog()
	else if picked is "Remove RenderBuddy…" then
		uninstallBB()
	else if picked is "Pair with STEVE again" then
		if isRunning() then do shell script "kill $(cat \"$HOME/.renderbuddy/connector.pid\") 2>/dev/null; sleep 1; true"
		if pairBB() then my launchConnector()
	end if
	return false
end moreChoices

on optionKeyHeld()
	try
		set flags to (current application's NSEvent's modifierFlags()) as integer
		return ((flags div 524288) mod 2) = 1
	on error
		return false
	end try
end optionKeyHeld

on run
	if optionKeyHeld() then
		moreChoices()
		return
	end if
	if not isPaired() then
		startBB()
		return
	end if
	repeat
		try
			if isRunning() then
				set choice to button returned of (display dialog "RenderBuddy is on and ready for STEVE." & return & return & "It turns itself off after a while with no jobs. Turning it off cancels any render in progress." buttons {"More…", "Turn off", "Done"} default button "Done" with title "RenderBuddy")
			else
				set choice to button returned of (display dialog "RenderBuddy is off." buttons {"More…", "Done", "Turn on"} default button "Turn on" with title "RenderBuddy")
			end if
		on error
			return
		end try
		if choice is "Turn on" then
			startBB()
			return
		else if choice is "Turn off" then
			stopBB()
			return
		else if choice is "More…" then
			if not moreChoices() then return
		else
			return
		end if
	end repeat
end run

on open location theURL
	if theURL contains "uninstall" then
		uninstallBB()
	else if theURL contains "stop" then
		if isRunning() then stopBB()
	else if isRunning() then
		display notification "Already on and ready for STEVE." with title "RenderBuddy"
	else
		startBB()
	end if
end open location
