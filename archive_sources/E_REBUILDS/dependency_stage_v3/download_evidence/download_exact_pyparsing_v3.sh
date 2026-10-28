#!/bin/sh
# One metadata-closure-approved RPM only, in a fresh pinned official-base container.
# No runtime installation, new key download, version fallback or scientific work.
set -eu
spec='python3-pyparsing-0:3.1.1-1.oe2403sp3.noarch'
key='/etc/pki/rpm-gpg/RPM-GPG-KEY-openEuler'
mkdir /stage/rpms
printf '%s  %s\n' '006e79d37c10e74c24df6d07c4efc4176515cec009daa5ed493b06f5b6ef39c1' "$key" | sha256sum -c - > /stage/bundled_key_sha256_check.txt 2>&1
rpm --import "$key" > /stage/bundled_key_local_import.txt 2>&1
rpm -q gpg-pubkey --qf '%{NAME}-%{VERSION}-%{RELEASE}\n' > /stage/actual_imported_public_keys.txt
dnf -v --disablerepo='*' --enablerepo=OS --enablerepo=update repolist --enabled > /stage/enabled_official_OS_update_repositories.txt 2>&1
action=install
existing=$(rpm -q python3-pyparsing --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' 2>/dev/null || true)
printf '%s\n' "$existing" > /stage/base_existing_pyparsing_nevra.txt
if [ "$existing" = "$spec" ]; then action=reinstall; fi
if dnf -y --disablerepo='*' --enablerepo=OS --enablerepo=update \
        --setopt=install_weak_deps=False "$action" --downloadonly --downloaddir=/stage/rpms \
        "$spec" > /stage/exact_pyparsing_download.log 2>&1; then
    :
else
    code=$?
    printf '%s\n' "Exact single-RPM download failed (exit $code); no fallback, tool installation or unpinning." >&2
    exit "$code"
fi
count=0
for package in /stage/rpms/*.rpm; do
    [ -f "$package" ] || continue
    count=$((count+1))
done
if [ "$count" -ne 1 ]; then
    printf '%s\n' "Expected exactly one closure-approved RPM, got $count; retain evidence and stop." >&2
    exit 1
fi
actual=$(rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' "$package")
printf '%s\n' "$actual" > /stage/actual_pyparsing_nevra.txt
[ "$actual" = "$spec" ] || { printf '%s\n' 'Actual RPM NEVRA differs; no completion claim.' >&2; exit 1; }
sha256sum "$package" > /stage/actual_pyparsing_sha256.txt
signature_code=0
rpm --checksig --verbose "$package" > /stage/actual_pyparsing_checksig.txt 2>&1 || signature_code=$?
printf '%s\n' "$signature_code" > /stage/actual_pyparsing_checksig_exit.txt
if [ "$signature_code" -ne 0 ]; then
    printf '%s\n' 'Actual checksig failed; retain evidence and stop for root.' >&2
    exit "$signature_code"
fi
printf '%s\n' 'Exactly one pinned RPM downloaded and recorded; actual root review is still required. No build/install/secret-free attestation.'
