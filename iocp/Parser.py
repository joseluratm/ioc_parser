#!/usr/bin/env python3

###################################################################################################
#
# Copyright (c) 2015, Armin Buescher (armin.buescher@googlemail.com)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
###################################################################################################
#
# File:             iocp.py
# Description:      IOC Parser is a tool to extract indicators of compromise from security reports
#                   in PDF format.
# Usage:            iocp.py [-h] [-p INI] [-f FORMAT] PDF
# Author:           Armin Buescher (@armbues)
# Contributors:     Angelo Dell'Aera (@angelodellaera)
# Thanks to:        Jose Ramon Palanco
#                   Koen Van Impe (@cudeso)
#
###################################################################################################

import os
import sys
import fnmatch
import glob
import re
try:
	import configparser as ConfigParser
except ImportError:
	import ConfigParser
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

# Import optional third-party libraries
IMPORTS = []
try:
	from PyPDF2 import PdfFileReader
	IMPORTS.append('pypdf2')
except ImportError:
	pass
try:
	from pdfminer.pdfpage import PDFPage
	from pdfminer.pdfinterp import PDFResourceManager
	from pdfminer.converter import TextConverter
	from pdfminer.pdfinterp import PDFPageInterpreter
	from pdfminer.layout import LAParams
	IMPORTS.append('pdfminer')
except ImportError:
	pass
try:
	from bs4 import BeautifulSoup
	IMPORTS.append('beautifulsoup')
except ImportError:
	pass
try:
	import requests
	IMPORTS.append('requests')
except ImportError:
	pass

# Import project source files
import iocp
from iocp import Output

class Parser(object):
	patterns = {}
	defang = {}

	def __init__(self, patterns_ini=None, input_format='pdf', dedup=False, library='pdfminer', output_format='csv', output_handler=None):
		basedir = iocp.get_basedir()

		if patterns_ini is None:
			patterns_ini = os.path.join(basedir, 'data/patterns.ini')
		self.load_patterns(patterns_ini)

		wldir = os.path.join(basedir, 'data/whitelists')
		self.whitelist = self.load_whitelists(wldir)

		self.dedup = dedup
		if output_handler:
			self.handler = output_handler
		else:
			self.handler = Output.getHandler(output_format)

		self.ext_filter = "*." + input_format
		parser_format = "parse_" + input_format
		try:
			self.parser_func = getattr(self, parser_format)
		except AttributeError:
			e = 'Selected parser format is not supported: %s' % (input_format)
			raise NotImplementedError(e)

		self.library = library
		if input_format == 'pdf':
			if library not in IMPORTS:
				e = 'Selected PDF parser library not found: %s' % (library)
				raise ImportError(e)
		elif input_format == 'html':
			if 'beautifulsoup' not in IMPORTS:
				e = 'HTML parser library not found: BeautifulSoup'
				raise ImportError(e)

	def load_patterns(self, fpath):
		config = ConfigParser.ConfigParser()
		with open(fpath) as f:
			config.readfp(f)

		for ind_type in config.sections():
			try:
				ind_pattern = config.get(ind_type, 'pattern')
			except:
				continue

			if ind_pattern:
				ind_regex = re.compile(ind_pattern)
				self.patterns[ind_type] = ind_regex

			try:
				ind_defang = config.get(ind_type, 'defang')
			except:
				continue

			if ind_defang:
				self.defang[ind_type] = True

	def load_whitelists(self, fpath):
		whitelist = {}

		searchdir = os.path.join(fpath, "whitelist_*.ini")
		fpaths = glob.glob(searchdir)
		for fpath in fpaths:
			t = os.path.splitext(os.path.split(fpath)[1])[0].split('_',1)[1]
			patterns = [line.strip() for line in open(fpath)]
			whitelist[t]  = [re.compile(p) for p in patterns]

		return whitelist

	def is_whitelisted(self, ind_match, ind_type):
		try:
			for w in self.whitelist[ind_type]:
				if w.findall(ind_match):
					return True
		except KeyError as e:
			pass
		return False

	def parse_page(self, fpath, data, page_num):
		for ind_type, ind_regex in self.patterns.items():
			matches = ind_regex.findall(data)

			for ind_match in matches:
				if isinstance(ind_match, tuple):
					ind_match = ind_match[0]

				if self.is_whitelisted(ind_match, ind_type):
					continue

				if ind_type in self.defang:
					ind_match = re.sub(r'\[\.\]', '.', ind_match)

				if self.dedup:
					if (ind_type, ind_match) in self.dedup_store:
						continue

					self.dedup_store.add((ind_type, ind_match))

				self.handler.print_match(fpath, page_num, ind_type, ind_match)

	def parse_pdf_pypdf2(self, f, fpath):
		try:
			pdf = PdfFileReader(f, strict = False)

			if self.dedup:
				self.dedup_store = set()

			self.handler.print_header(fpath)
			page_num = 0
			for page in pdf.pages:
				page_num += 1

				data = page.extractText()

				self.parse_page(fpath, data, page_num)
			self.handler.print_footer(fpath)
		except (KeyboardInterrupt, SystemExit):
			raise

	def parse_pdf_pdfminer(self, f, fpath):
		try:
			laparams = LAParams()
			laparams.all_texts = True  
			rsrcmgr = PDFResourceManager()
			pagenos = set()

			if self.dedup:
				self.dedup_store = set()

			self.handler.print_header(fpath)
			page_num = 0
			for page in PDFPage.get_pages(f, pagenos, check_extractable=True):
				page_num += 1

				retstr = StringIO()
				device = TextConverter(rsrcmgr, retstr, codec='utf-8', laparams=laparams)
				interpreter = PDFPageInterpreter(rsrcmgr, device)
				interpreter.process_page(page)
				data = retstr.getvalue()
				retstr.close()

				self.parse_page(fpath, data, page_num)
			self.handler.print_footer(fpath)
		except (KeyboardInterrupt, SystemExit):
			raise

	def parse_pdf(self, f, fpath):
		parser_format = "parse_pdf_" + self.library
		try:
			self.parser_func = getattr(self, parser_format)
		except AttributeError:
			e = 'Selected PDF parser library is not supported: %s' % (self.library)
			raise NotImplementedError(e)
			
		self.parser_func(f, fpath)

	def parse_txt(self, f, fpath):
		try:
			if self.dedup:
				self.dedup_store = set()

			data = f.read()
			self.handler.print_header(fpath)
			self.parse_page(fpath, data, 1)
			self.handler.print_footer(fpath)
		except (KeyboardInterrupt, SystemExit):
			raise

	def parse_html(self, f, fpath):
		try:
			if self.dedup:
				self.dedup_store = set()
				
			data = f.read()
			soup = BeautifulSoup(data)
			html = soup.findAll(text=True)

			text = u''
			for elem in html:
				if elem.parent.name in ['style', 'script', '[document]', 'head', 'title']:
					continue
				elif re.match('<!--.*-->', unicode(elem)):
					continue
				else:
					text += unicode(elem)

			self.handler.print_header(fpath)
			self.parse_page(fpath, text, 1)
			self.handler.print_footer(fpath)
		except (KeyboardInterrupt, SystemExit):
			raise

	def parse(self, path):
		try:
			if path.startswith('http://') or path.startswith('https://'):
				if 'requests' not in IMPORTS:
					e = 'HTTP library not found: requests'
					raise ImportError(e)
				headers = { 'User-Agent': 'Mozilla/5.0 Gecko Firefox' }
				r = requests.get(path, headers=headers)
				r.raise_for_status()
				f = StringIO(r.content)
				self.parser_func(f, path)
				return
			elif os.path.isfile(path):
				with open(path, 'rb') as f:
					self.parser_func(f, path)
				return
			elif os.path.isdir(path):
				for walk_root, walk_dirs, walk_files in os.walk(path):
					for walk_file in fnmatch.filter(walk_files, self.ext_filter):
						fpath = os.path.join(walk_root, walk_file)
						with open(fpath, 'rb') as f:
							self.parser_func(f, fpath)
				return

			e = 'File path is not a file, directory or URL: %s' % (path)
			raise IOError(e)
		except (KeyboardInterrupt, SystemExit):
			raise
		except Exception as e:
			self.handler.print_error(path, e)
