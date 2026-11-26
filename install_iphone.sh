#!/bin/bash
# install_iphone.sh
# 「満点で感じる」(iPhone のテスト用のアプリ。TODO 58)をビルドして、つないだ iPhone に入れて開く
#   中身は ios/UsuMantenFeel/install.sh(説明はそちらと ios/UsuMantenFeel/README.md)
#
#     ./install_iphone.sh [端末の UDID]
exec "$(cd "$(dirname "$0")" && pwd)/ios/UsuMantenFeel/install.sh" "$@"
