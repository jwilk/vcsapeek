#!/bin/sh

# Copyright © 2022 Jakub Wilk <jwilk@jwilk.net>
# SPDX-License-Identifier: MIT

set -e -u
echo 1..7
here="${0%/*}"
pdir="${0%/*}/.."
src="$pdir/linuxvt.py"
dst="$here/const.c"
n=1
while read -r line
do
    case $line in
    '# From <'*)
        header=${line#*<}
        header=${header%>:}
        ;;
    [A-Z]*' = '[0-9]*)
        var=${line%' = '*}
        val=${line#*' = '}
        printf '#include <assert.h>\n' > "$dst"
        printf '#include <%s>\n' "$header" >> "$dst"
        printf 'static_assert(%s == %s, "%s");\n' "$var" "$val" "$var" >> "$dst"
        if cc -o /dev/null -c "$dst"
        then
            echo "ok $n $var"
        else
            echo "not ok $n $var"
        fi
        n=$((n + 1))
        ;;
    esac
done < "$src"
rm -f "$dst"

# vim:ts=4 sts=4 sw=4 et ft=sh
