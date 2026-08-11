# Local PptxGenJS security patch

This directory vendors the runtime bundle from **PptxGenJS 4.0.1** under its
MIT license. The upstream package declares `image-size` as a dependency, but
the published CommonJS bundle used by this skill does not import or reference
it. The dependency currently pulls in two high-severity denial-of-service
advisories (`GHSA-w3rx-r6r6-pgpr` and `GHSA-5p2g-fcmc-qvqq`), and no fixed
upstream PptxGenJS release is available for the current API.

The vendored package therefore keeps the tested 4.0.1 runtime bundle and
removes that unused dependency from its package metadata. It is intentionally
limited to the CommonJS entry point used by `create_drawio_pptx.cjs`.

When upgrading PptxGenJS:

1. obtain the upstream release from npm;
2. verify the bundle does not reintroduce `image-size` or another vulnerable
   image parser without a fixed version;
3. update this package and `package-lock.json` together;
4. run `npm ci --ignore-scripts --no-audit`, `npm audit --audit-level=high`,
   and the PPTX generator smoke test.
