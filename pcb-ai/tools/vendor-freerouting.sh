#!/usr/bin/env bash
# Install Freerouting and a JRE that can run it, without root.
#
# Two facts cost a cycle each and are worth stating:
#
#   1. Freerouting 2.3.0's classes are **Java 25** (class file version 69). A Java 21
#      runtime refuses them with `UnsupportedClassVersionError ... recognizes class file
#      versions up to 65.0`, which reads like a corrupt jar rather than a version floor.
#      This box ships Java 8.
#   2. The release's Linux installer is **x86-64 only**. On aarch64 it is the
#      platform-independent .jar plus an aarch64 JRE — which works fine.
set -euo pipefail
cd "$(dirname "$0")/../vendor" 2>/dev/null || { mkdir -p "$(dirname "$0")/../vendor"; cd "$(dirname "$0")/../vendor"; }

FR_VERSION="${FR_VERSION:-2.3.0}"
JAVA_MAJOR="${JAVA_MAJOR:-25}"
ARCH="$(uname -m)"; [ "$ARCH" = "aarch64" ] && ARCH=aarch64 || ARCH=x64

if [ ! -f freerouting.jar ]; then
  echo "fetching freerouting ${FR_VERSION}"
  curl -fsSL -o freerouting.jar \
    "https://github.com/freerouting/freerouting/releases/download/v${FR_VERSION}/freerouting-${FR_VERSION}.jar"
fi

if ! ls -d jdk-${JAVA_MAJOR}* >/dev/null 2>&1; then
  echo "fetching Temurin JRE ${JAVA_MAJOR} (${ARCH})"
  curl -fsSL -o jre.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/${JAVA_MAJOR}/ga/linux/${ARCH}/jre/hotspot/normal/eclipse"
  tar xzf jre.tar.gz && rm -f jre.tar.gz
fi

JRE="$(ls -d jdk-${JAVA_MAJOR}* | head -1)"
"./${JRE}/bin/java" -version 2>&1 | head -1
"./${JRE}/bin/java" -jar freerouting.jar --help >/dev/null 2>&1 \
  && echo "freerouting ${FR_VERSION} ready" \
  || { echo "freerouting did not start" >&2; exit 1; }
