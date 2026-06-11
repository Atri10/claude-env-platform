# launchd jobs (macOS) for nightly memory maintenance

Create `~/Library/LaunchAgents/com.claude-env.memory.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.claude-env.memory</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-c</string>
    <string>python3 $HOME/.claude-env/scripts/nightly_memory.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardErrorPath</key><string>/tmp/claude-env.memory.err</string>
  <key>StandardOutPath</key><string>/tmp/claude-env.memory.out</string>
</dict>
</plist>
```

Then: `launchctl load ~/Library/LaunchAgents/com.claude-env.memory.plist`
