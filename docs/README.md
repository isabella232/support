# SuPPort documentation setup

[SuPPort][support]'s documentation is built with [Sphinx][sphinx] and
hosted on [GitHub pages][gh_pages] at
[paypal.github.io/support][support_docs]. The recommended process for
updating the documentation closely follows [this
guide][sphinx_docs_github] and, for development convience, is
partially duplicated here in brief.

[support]: https://github.com/paypal/support
[sphinx]: http://sphinx-doc.org/
[gh_pages]: https://pages.github.com/
[support_docs]: http://paypal.github.io/support/
[sphinx_docs_github]: http://daler.github.io/sphinxdoc-test/includeme.html

The key is to perform two separate git clones of the SuPPort repo. The
first clone remains on the `master` branch for normal development on
the code and raw documents you see here.

The other will be used to generate the generated docs (via git
commit/push to Github). The process to set up this repo is detailed in
the aforementioned guide, under [Setting up cloned repos on another
machine][setting_up_repos]. We start by changing directories to the
**parent** directory of the SuPPort code repository and execute the
following commands:

```
mkdir support-docs
cd support-docs
git clone git@github.com:paypal/support.git html
cd html
git checkout -b gh-pages remotes/origin/gh-pages
```

And you're done! Note that this new `support-docs` repository
generally should not be updated manually. Make any documentation
changes in the normal `support` repo/branch and then execute the
following command to regenerate the docs:

```
# from /home/myuser/myworkspace/support/docs, not from the support-docs repo
make buildandcommithtml
```

This will generate the documentation, make a new git commit on the
gh-pages branch, and push this commit to Github.

## Local generation

Before pushing, you probably want to first check your changes
locally. First install the documentation requirements by executing the
following in the `support` repo root:

```
pip install -r requirements-docs.txt
```

Once you have all of the above set up, doing so is easy! Use the `make
html` command and `python -m SimpleHTTPServer` in the build directory,
or simply use `sphinx-autobuild` in the `support/docs` directory.

[setting_up_repos]: http://daler.github.io/sphinxdoc-test/includeme.html#setting-up-cloned-repos-on-another-machine
