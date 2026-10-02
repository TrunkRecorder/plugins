<!-- Adding or updating a plugin? Run `./add-release <repo URL> <tag>`, then `./check plugins/<id>.json`, and commit both plugins/<id>.json and index.json. -->

**Plugin:** <!-- repository URL and the release's tag -->

**New, or an update?** <!-- for an update: what changed -->

### For the reviewer

- [ ] The source is public, and its license allows it to be listed
- [ ] It does what it says, and sends nothing anywhere the user didn't configure
- [ ] Secrets are marked `x-secret`, and never logged
- [ ] It copes without M4A, if it asks for it
- [ ] It starts with its default settings, or says what to set
- [ ] The id is fitting and doesn't impersonate anyone
- [ ] For an update: read the compare from the old entry's commit to the new one
