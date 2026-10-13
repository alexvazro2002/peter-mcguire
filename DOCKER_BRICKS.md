# MeTube and Stirling-PDF

The Tools page now has two one-click bricks. Clicking either brick starts Docker
Desktop if needed, starts or creates the container, waits for the application to
be ready, and opens the default browser. The panel shows startup and image
download output. No second launch click is needed.

## Requirements and installation

1. Docker Desktop must already be installed and its first-time setup completed.
   Use **Linux containers**. You do not need to keep Docker open manually.
2. Close NexusDesk. Extract `NexusDesk_Docker_Bricks.zip` to a normal folder.
3. Run `ADD_DOCKER_BRICKS.bat`. It installs into
   `C:\Users\Alex\Desktop\NexusDesk` and reopens the panel.
4. Open **Tools** and click **MeTube** or **Stirling-PDF**.

First launch needs internet and may take several minutes to download the Docker
image. Later launches reuse the installed image. An error stays visible in the
panel log; the browser opens only after readiness has been checked.

The installer targets NexusDesk v2.3. It verifies the three shared app files
before changing anything and keeps a backup. Unknown custom changes cause it to
stop before changing files. No dependency reinstall is required. Set
`NEXUSDESK_ROOT` before running the installer if the app is in another folder.

## Addresses and storage

| Brick | Default browser address | Persistent data |
| --- | --- | --- |
| MeTube | http://127.0.0.1:8082/ | `%USERPROFILE%\Downloads\MeTube` |
| Stirling-PDF | http://127.0.0.1:8090/ | `services\stirling_pdf\` under NexusDesk |

The AI server on port 8081 is unaffected. If a default port is occupied, a free
port in the following 39 ports is selected and the actual address appears in the
log. An existing compatible `metube` container is reused, including its port and
download location. Existing containers are never deleted or recreated by launch.

New containers listen on **127.0.0.1** only. Stirling-PDF uses no-login mode for
this local desktop use. An older MeTube container keeps its original bindings.

Both services stay running when the panel closes. **Stop service** stops the
selected container while retaining all data. **Cancel launch** cancels the
launcher, and does not promise to stop a container already started. Only one tool
launcher runs at a time; once a browser opens, another brick can be launched.

Stirling-PDF OCR language data is kept in the Docker named volume
`nexusdesk-stirling-tessdata`. A named volume preserves the image's bundled
languages on first creation; an empty host folder would hide them. Back up this
volume as well as `services\stirling_pdf` if you need a complete backup.

## Full upstream source

The ZIP includes the complete original GitHub source archive pinned to commit
`d43bbacca58ee3acc4416fe9cfad78923a88e3a4`, downloaded on October 2, 2026. The
installer extracts it into `projects\Stirling-PDF`, retaining license files and
copyright notices. Its documentation symlink is materialised for Windows.
If that folder already contains your own checkout, the installer uses
`projects\Stirling-PDF-upstream-d43bbacca58e` instead and keeps your checkout.

The brick runs the official prebuilt standard image:
`docker.stirlingpdf.com/stirlingtools/stirling-pdf:latest`.
This includes the normal PDF workbench and its dependencies, without requiring
you to build Java and the frontend. The included source snapshot and the
downloaded release image may differ. Editing the snapshot does not change the
running prebuilt image. Follow the upstream DeveloperGuide and
`docker/embedded/Dockerfile` in the snapshot if you want to build a custom image.

Stirling-PDF is open-core: its main license is MIT with explicitly listed
directories under separate licenses. Those licenses are retained unchanged.
This integration does not remove licensing restrictions or unlock paid features.

Upstream references:

- https://github.com/Stirling-Tools/Stirling-PDF
- https://docs.stirlingpdf.com/Installation/Docker%20Install/
- https://github.com/alexta69/metube

## Updates and recovery

The launcher deliberately does not pull/recreate containers on each click.
To update, stop the service, pull its official image in Docker Desktop, and
recreate the container with the same ports, environment and mounts. Keep all
volumes and bind folders. Existing MeTube custom settings must be preserved.
For a new container created by these bricks, you can remove only that stopped
container in Docker Desktop (keep its volumes), then click its brick to recreate
it using the downloaded image.

Panel file backups are in `brick-backups\<timestamp>`. To restore one, close the
panel and run the included installer with `--restore`:

```bat
py -3 installer.py --restore "C:\Users\Alex\Desktop\NexusDesk\brick-backups\YOUR_BACKUP"
```

Restore refuses to overwrite files modified after installation. Source code,
downloads, Docker data, notes, calendar, settings, model weights and AI history
are retained.

## Validation limits

Automated checks cover the panel integration, Docker command construction,
existing-container reuse, port selection, failure reporting, actual local HTTP
readiness requests, Windows Docker Desktop startup orchestration, and installer
backup/rollback/restore. This build environment has no Docker daemon or Windows
desktop: live upstream containers and Docker Desktop must be verified on your PC.
