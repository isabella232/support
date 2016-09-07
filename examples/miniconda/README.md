# Laying out the Miniconda packaging

## Overview
```
.
├── Miniconda-latest-Linux-x86_64.sh
├── README.md
├── packaging
│   └── scripts
│       └── activate
└── archives
    ├── patchelf-0.8-0.tar.bz2
    └── ...
```
## Steps

1. make the archives, packaging, and packaging/scripts directories
2. Download+Install Miniconda
3. Use that miniconda installation to install and verify desired packages (e.g., numpy)
4. Copy Miniconda.sh into the root pkg directory
5. Copy all .bz2 files from your Miniconda installation (`<install_path>/pkgs/*.bz2`) into the new archives directory (verify patchelf is there. any version will work, as long as it matches the pattern `patchelf*.bz2`)
6. Make sure the activate script is in place and permissioned correctly
