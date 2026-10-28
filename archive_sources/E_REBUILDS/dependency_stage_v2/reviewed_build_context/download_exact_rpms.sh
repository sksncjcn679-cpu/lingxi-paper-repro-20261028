#!/bin/sh
# Execute only in a NEW official03b3 container after root actually verifies quiet.
# Downloads only. No tool installation, version substitution, source unpack or pip.
set -eu
stage=/stage
if [ -e "$stage/rpms" ]; then
    printf '%s\n' 'Refusing existing rpms directory; retain it and use fresh staging.' >&2
    exit 1
fi
mkdir "$stage/rpms"
: > "$stage/already_exact_specs.txt"
: > "$stage/base_requested_nevra.txt"
set --
while IFS= read -r spec || [ -n "$spec" ]; do
    [ -n "$spec" ] || continue
    set -- "$@" "$spec"
    name=${spec%%-[0-9]*:*}
    actual=$(rpm -q --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' "$name" 2>/dev/null || true)
    printf '%s\t%s\n' "$spec" "$actual" >> "$stage/base_requested_nevra.txt"
    if [ "$actual" = "$spec" ]; then
        printf '%s\n' "$spec" >> "$stage/already_exact_specs.txt"
    fi
done < "$stage/rpm_specs.txt"
if [ "$#" -ne 14 ]; then
    printf '%s\n' 'Exactly the 14 reviewed explicit NEVRA specs are required.' >&2
    exit 1
fi
dnf -v repolist --enabled > "$stage/enabled_repositories.txt" 2>&1
if dnf -y --setopt=install_weak_deps=False install --downloadonly \
        --downloaddir="$stage/rpms" "$@" > "$stage/download_install.log" 2>&1; then
    :
else
    code=$?
    printf '%s\n' "Exact download failed (exit $code); inspect download_install.log. Unsupported --downloadonly or unavailable pins are fatal; no fallback or tool installation." >&2
    exit "$code"
fi
# install --downloadonly skips packages already exact in this immutable base.
# Explicit download-only reinstall fetches those targets without installing them.
if [ -s "$stage/already_exact_specs.txt" ]; then
    set --
    while IFS= read -r spec || [ -n "$spec" ]; do
        [ -n "$spec" ] && set -- "$@" "$spec"
    done < "$stage/already_exact_specs.txt"
    if dnf -y --setopt=install_weak_deps=False reinstall --downloadonly \
            --downloaddir="$stage/rpms" "$@" > "$stage/download_reinstall.log" 2>&1; then
        :
    else
        code=$?
        printf '%s\n' "Exact download-only reinstall failed (exit $code); inspect download_reinstall.log. No fallback or version changes." >&2
        exit "$code"
    fi
fi
: > "$stage/rpm_nevra.txt"
: > "$stage/rpm_sha256.txt"
: > "$stage/rpm_checksig.txt"
: > "$stage/rpm_checksig_exit_codes.tsv"
signature_nonzero=0
found=0
for package in "$stage"/rpms/*.rpm; do
    [ -f "$package" ] || continue
    found=$((found+1))
    rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' "$package" >> "$stage/rpm_nevra.txt"
    sha256sum "$package" >> "$stage/rpm_sha256.txt"
    printf '\nFILE %s\n' "${package##*/}" >> "$stage/rpm_checksig.txt"
    signature_code=0
    rpm --checksig "$package" >> "$stage/rpm_checksig.txt" 2>&1 || signature_code=$?
    printf '%s\t%s\n' "${package##*/}" "$signature_code" >> "$stage/rpm_checksig_exit_codes.tsv"
    [ "$signature_code" -eq 0 ] || signature_nonzero=1
done
[ "$found" -gt 0 ] || { printf '%s\n' 'No RPM files downloaded.' >&2; exit 1; }
while IFS= read -r spec || [ -n "$spec" ]; do
    [ -n "$spec" ] || continue
    if ! grep -F -x -- "$spec" "$stage/rpm_nevra.txt" > /dev/null; then
        printf '%s\n' "Missing exact target RPM: $spec; no completion claim." >&2
        exit 1
    fi
done < "$stage/rpm_specs.txt"
printf '%s\n' 'Downloads and actual metadata recorded. Root must review enabled official repositories, dependency closure, signatures/trusted key provenance and SHA before any image build. Exit0 is not a trusted-signature or secret-free certificate.'
if [ "$signature_nonzero" -ne 0 ]; then
    printf '%s\n' 'At least one rpm --checksig exit code is nonzero; retained evidence requires root review.' >&2
    exit 2
fi
