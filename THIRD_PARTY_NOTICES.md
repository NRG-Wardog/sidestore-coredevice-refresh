# Third-Party Notices and License Scope

This repository combines original builder scripts and documentation with build-time integration against upstream open-source projects.

## Repository-authored material

Unless a file says otherwise, original material authored specifically for this repository — including the builder scripts, tests, and documentation — is distributed under the MIT License in [`LICENSE`](LICENSE).

## Upstream projects

The build pipeline checks out pinned revisions of third-party projects at build time. Those projects retain their own copyright and license terms.

Important upstream components include:

- [SideStore/SideStore](https://github.com/SideStore/SideStore) — upstream SideStore source and SideStore-derived application code; consult the upstream repository for its current license terms.
- [SideStore/minimuxer](https://github.com/SideStore/minimuxer) — device connectivity and integration code; consult the upstream repository for its license terms.
- [SideStore/idevice](https://github.com/SideStore/idevice) — CoreDevice/device-service implementation; consult the upstream repository for its license terms.
- [SideStore/jktcp](https://github.com/SideStore/jktcp) — userspace TCP implementation; consult the upstream repository for its license terms.

## Distributed IPA

The prebuilt IPA is a modified SideStore binary produced from upstream SideStore and its dependencies plus the public patches in this repository. The MIT license in this repository does **not** replace or override licenses that apply to upstream or third-party code contained in the resulting binary.

Anyone redistributing, modifying, or incorporating the resulting binary or upstream-derived code should review and comply with the applicable upstream license terms.

This notice is intended to make the source and license boundaries explicit; it does not change any third-party license.
