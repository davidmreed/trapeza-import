==============
Trapeza-Import
==============

``trapeza-import`` is a web application based on Flask whose purpose
is to facilitate the merging of tabular record data containing 
duplicates and wherein record identifiers are missing from some data.

``trapeza-import`` is primarily aimed at importing records into Raiser's
Edge, a fundraising database package. However, other database use 
cases are possible.

``trapeza-import`` accepts master, incoming, and profile spreadsheets 
(as used with ``trapeza-match`` from the ``trapeza`` package) and 
presents a simple HTML-based user interface to enable to user to select 
possible matches and merge conflicting data, outputting a file which 
contains matched record identifiers. For speed reasons, it is *strongly*
 recommended to use a master sheet that has been preprocessed with 
 ``trapeza-process``.

This application is explicitly not intended to be run in a public-facing
manner. It should be used *only* as an in-house application. There are 
numerous security issues in ``trapeza-import`` that preclude its use
on a publicly-accessible server.

``trapeza-import`` requires ``trapeza`` and ``Flask``. Templates use
``jQuery`` but this library need not be installed if an Internet 
connection is active.
