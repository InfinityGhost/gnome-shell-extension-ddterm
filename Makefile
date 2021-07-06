SHELL := /bin/bash

export PATH := $(abspath node_modules/.bin):$(PATH)

# run 'make WITH_GTK4=no' to disable Gtk 4/GNOME 40 support
# (could be necessary on older distros without gtk4-builder-tool)
WITH_GTK4 := yes

all: schemas/gschemas.compiled lint pack gtk-builder-validate

.PHONY: all

SCHEMAS := $(wildcard schemas/*.gschema.xml)

schemas/gschemas.compiled: $(SCHEMAS)
	glib-compile-schemas --strict $(dir $@)

lint/eslintrc-gjs.yml:
	curl -o $@ 'https://gitlab.gnome.org/GNOME/gjs/-/raw/8c50f934bc81f224c6d8f521116ddaa5583eef66/.eslintrc.yml'

lint: lint/eslintrc-gjs.yml
	eslint .

.PHONY: lint

handlebars.js: node_modules/handlebars/dist/handlebars.min.js
	cp $< $@

GTK3_ONLY_UI := $(filter-out prefs.ui,$(patsubst glade/%,%,$(wildcard glade/*.ui)))

$(GTK3_ONLY_UI): %.ui: glade/%.ui
	gtk-builder-tool simplify $< >$@

prefs-gtk3.ui: glade/prefs.ui
	gtk-builder-tool simplify $< >$@

GENERATED_SOURCES := $(GTK3_ONLY_UI) prefs-gtk3.ui handlebars.js

tmp:
	mkdir -p tmp

tmp/prefs-3to4.ui: prefs-gtk3.ui | tmp
	gtk4-builder-tool simplify --3to4 $< >$@

tmp/prefs-3to4-fixup.ui: tmp/prefs-3to4.ui 3to4-fixup.xsl | tmp
	xsltproc 3to4-fixup.xsl $< >$@

prefs-gtk4.ui: tmp/prefs-3to4-fixup.ui
	gtk4-builder-tool simplify $< >$@

ifeq ($(WITH_GTK4),yes)
GENERATED_SOURCES += prefs-gtk4.ui
endif

gtk-builder-validate/%: %
	gtk-builder-tool validate $<

.PHONY: gtk-builder-validate/%

gtk-builder-validate/prefs-gtk4.ui: prefs-gtk4.ui
	gtk4-builder-tool validate $<

.PHONY: gtk-builder-validate/prefs-gtk4.ui

DEFAULT_SOURCES := extension.js prefs.js metadata.json

EXTRA_SOURCES := $(filter-out test-prefs-gtk4.js extension_tests.js,$(wildcard *.js *.css))
EXTRA_SOURCES += com.github.amezin.ddterm com.github.amezin.ddterm.Extension.xml
EXTRA_SOURCES += menus.ui
EXTRA_SOURCES += LICENSE

EXTRA_SOURCES := $(filter-out $(DEFAULT_SOURCES), $(sort $(GENERATED_SOURCES) $(EXTRA_SOURCES)))

gtk-builder-validate: $(addprefix gtk-builder-validate/, $(filter-out terminalpage.ui,$(filter %.ui,$(EXTRA_SOURCES))))

.PHONY: gtk-builder-validate

EXTENSION_UUID := ddterm@amezin.github.com
DEVELOP_SYMLINK := $(HOME)/.local/share/gnome-shell/extensions/$(EXTENSION_UUID)

test-deps: schemas/gschemas.compiled $(GENERATED_SOURCES)

.PHONY: test-deps

develop: test-deps
	mkdir -p "$(dir $(DEVELOP_SYMLINK))"
	@if [[ -e "$(DEVELOP_SYMLINK)" && ! -L "$(DEVELOP_SYMLINK)" ]]; then \
		echo "$(DEVELOP_SYMLINK) exists and is not a symlink, not overwriting"; exit 1; \
	fi
	if [[ "$(abspath .)" != "$(abspath $(DEVELOP_SYMLINK))" ]]; then \
		ln -snf "$(abspath .)" "$(DEVELOP_SYMLINK)"; \
	fi

.PHONY: develop

develop-uninstall:
	if [[ -L "$(DEVELOP_SYMLINK)" ]]; then \
		unlink "$(DEVELOP_SYMLINK)"; \
	fi

.PHONY: develop-uninstall

prefs enable disable reset info show:
	gnome-extensions $@ $(EXTENSION_UUID)

.PHONY: prefs enable disable reset info show

EXTENSION_PACK := $(EXTENSION_UUID).shell-extension.zip
$(EXTENSION_PACK): $(SCHEMAS) $(EXTRA_SOURCES) $(DEFAULT_SOURCES)
	gnome-extensions pack -f $(addprefix --schema=,$(SCHEMAS)) $(addprefix --extra-source=,$(EXTRA_SOURCES)) .

pack: $(EXTENSION_PACK)
.PHONY: pack

install: $(EXTENSION_PACK) develop-uninstall
	gnome-extensions install -f $<

.PHONY: install

uninstall: develop-uninstall
	gnome-extensions uninstall $(EXTENSION_UUID)

.PHONY: uninstall

toggle quit:
	gapplication action com.github.amezin.ddterm $@

.PHONY: toggle quit

clean:
	$(RM) $(EXTENSION_PACK) $(filter-out handlebars.js,$(GENERATED_SOURCES)) schemas/gschemas.compiled $(wildcard tmp/*)

.PHONY: clean
