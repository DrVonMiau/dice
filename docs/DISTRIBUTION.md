# Distributing Dice

Dice isn't on Flathub (their guidelines exclude AI-assisted projects), so it
ships through GitHub instead. Two paths are set up, and they can coexist.

## 1. Single-file bundle on Releases (active now)

The simplest path: every tagged release carries a `.flatpak` bundle users can
download and double-click.

**How it works** — `.github/workflows/bundle.yml` runs when you push a tag
starting with `v` (e.g. `v0.3.0`). It builds the app in the GNOME 49 Flatpak
runtime and attaches `io.github.drvonmiau.Dice.flatpak` to that tag's release.

**To cut a release**

1. Bump the version in `meson.build` and add a `<release>` entry to
   `data/io.github.drvonmiau.Dice.metainfo.xml.in`.
2. Create the release + tag on GitHub (Releases → Draft a new release → choose
   or create tag `vX.Y.Z` → Publish). Publishing the tag triggers the workflow;
   a couple of minutes later the bundle appears as a release asset.
   (You can also push the tag from the CLI and the workflow still runs — it just
   needs a matching release to attach to, which `softprops/action-gh-release`
   creates if missing.)

**What users do**

```sh
flatpak install --user io.github.drvonmiau.Dice.flatpak
flatpak run io.github.drvonmiau.Dice
```

Trade-off: no automatic updates — users re-download to upgrade. That's what
path 2 solves.

## 2. Hosted Flatpak repo on GitHub Pages (active)

A signed Flatpak repository served from <https://drvonmiau.github.io/dice/>.
Users add it once and then get updates through GNOME Software /
`flatpak update` like any store app.

**How it works** — `.github/workflows/flatpak-repo.yml` runs when a release is
published (or by hand from the Actions tab). It builds the app with
[Flatter](https://github.com/andyholmes/flatter), signs the repository with the
`FLATPAK_GPG_KEY` secret, and deploys it to Pages together with:

- `index.flatpakrepo` — the repository description (with the public key);
- `io.github.drvonmiau.Dice.flatpakref` — one-click install for GNOME Software;
- `index.html` — the landing page, from `pages/index.html`.

**Setup (done once)**

1. **Settings → Pages → Source: "GitHub Actions".**
2. A signing key without a passphrase (the workflow can't type one):
   ```sh
   gpg --quick-gen-key "Dice <you@example.com>" default default never
   gpg --list-secret-keys --keyid-format long      # the ID after "ed25519/"
   gpg --armor --export-secret-keys <KEYID>        # copy into the secret below
   ```
3. Repository secret `FLATPAK_GPG_KEY` = the whole private-key block.
   Keep an encrypted backup of the key (and of the revocation certificate in
   `~/.gnupg/openpgp-revocs.d/`): losing it means users must re-add the repo.

**Cutting a release** — publishing a release on GitHub now does both paths:
the bundle is attached to the release (section 1) and the repository is
updated (this section).

**What users do**

```sh
flatpak remote-add --user --if-not-exists dice https://drvonmiau.github.io/dice/index.flatpakrepo
flatpak install --user dice io.github.drvonmiau.Dice
```

or the Install button on the landing page.
