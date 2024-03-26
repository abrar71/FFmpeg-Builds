#!/bin/bash

SCRIPT_REPO="https://github.com/madler/zlib.git"
SCRIPT_COMMIT="99b229487c2997b4b22eaef90fedfbff4d8826cc"

ffbuild_enabled() {
    return 0
}

ffbuild_dockerbuild() {
    local myconf=(
        --prefix="$FFBUILD_PREFIX"
        --static
    )

    ./configure "${myconf[@]}"
    make -j$(nproc)
    make install
}

ffbuild_configure() {
    echo --enable-zlib
}

ffbuild_unconfigure() {
    echo --disable-zlib
}
