FROM tb-seed-tabulate:bf58e37e6b35
# Apply the seeded defect at build time. /repo/.git is removed in the same
# layer: with it present, `git diff` inside the container would reveal the
# exact mutation. The patch file is removed too.
COPY broken.patch /tmp/broken.patch
RUN cd /repo && git apply /tmp/broken.patch \
    && rm -rf /repo/.git /repo/test /tmp/broken.patch
# Ordinary developer tests, EXCLUDING the held-out flipped tests.
COPY test/ /repo/test/
