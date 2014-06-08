#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  trapeza-import.py
#  
#  Copyright 2013-2014 David Reed <david@ktema.org>
#

import trapeza, trapeza.match, tempfile, pickle, cStringIO, os
from flask import Flask, request, session, make_response, render_template, abort
from operator import itemgetter, attrgetter
from itertools import groupby

# Accept three Source objects. Run match_incoming_records() on them.
# Render a template showing the user matches and allowing them to select a 
# matching record.
# On selection, run diff on the selected records (comparing the columns that are 
# mapped by the profile) and offer the user the chance to select one of the two versions 
# of each field or to enter their own data.
# On output, generate a copy of the input sheet with a unique ID column added specifying
# the matched record in the database. For each diffed column, if the user did not choose 
# to retain existing data, include the user-input value or the value from incoming.
# Maintain all other columns in the original input sheet unaltered for import.

app = Flask(__name__)
app.secret_key = "THIS_IS_A_TESTING_SECRET_KEY"

def flatten_record(record, headers):
    return "; ".join(["{0}: {1}".format(key, record.values[key]) for key in headers])

def generate_header_mapping(profile):
    header_map = {}
    
    for mapping in profile.mappings:
        if not mapping.key in header_map:
            header_map[mapping.key] = mapping.master_key
    
    return header_map

def group_results(results): 
    list_results = []
    
    gb = groupby(results, lambda res: res.incoming.input_line)
    
    for (k, g) in gb:
        list_results.append((k, sorted(list(g), key=attrgetter("score"), reverse=True)))
    
    return sorted(list_results, key=itemgetter(0))

def run_comparison(master, incoming, profile, cutoff):
    results = group_results(profile.compare_sources(master, incoming, cutoff))
    
    return results

@app.route("/")
def start():
    return render_template("index.html")

@app.route("/run", methods=["POST"])
def step_one():

    # Load files. We assume, at present, that the files are CSV format.
    master = trapeza.load_source(request.files["master"], "csv")
    incoming = trapeza.load_source(request.files["incoming"], "csv")
    profile = trapeza.match.Profile(source = trapeza.load_source(request.files["profile"], "csv"))
    
    # Determine primary key and set in Master. We do not set the primary key on incoming or the output.
    # If the user feels like matching multiple input lines to the same master records, that's fine;
    # we don't want to throw an exception.
    primary_key = request.form["primary_key"]
    master.set_primary_key(primary_key)
    
    # Bring in the parameters. FIXME: will this crash if a non-integer parameter is submitted?
    cutoff = int(request.form["cutoff"]) or 0
    display_diff = True if "display_diff" in request.form else False
    include_unmatched_records = True if "include_unmatched_records" in request.form else False
    output_only_modified_entries = True if "output_only_modified_entries" in request.form else False
    
    # Perform the comparison.
    results = group_results(profile.compare_sources(master, incoming, cutoff))
    
    # Save the details of the operation
    operation = {   "master": master, "incoming": incoming, "profile": profile, "primary_key": primary_key, "results": results,
                    "cutoff": cutoff, "display_diff": display_diff, "include_unmatched_records": include_unmatched_records, 
                    "output_only_modified_entries": output_only_modified_entries }

    outfile = tempfile.NamedTemporaryFile(delete=False)
    session["file"] = outfile.name
    pickle.dump(operation, outfile, pickle.HIGHEST_PROTOCOL)
    outfile.close()

    
    return render_template("match.html", 
                           results=results, 
                           primary_key=primary_key,
                           header_map=generate_header_mapping(profile),
                           incoming_headers=incoming.headers(),
                           master_headers=master.headers(),
                           flatten_record=flatten_record,
                           display_diff=display_diff or True)


@app.route("/dl", methods=["POST"])
def step_two():
    with open(session["file"], "rb") as sess_file:
        operation = pickle.load(sess_file)
        
    os.unlink(session["file"])
    
    primary_key = operation["primary_key"]
    header_map = generate_header_mapping(operation["profile"])
    
    output = trapeza.Source(operation["incoming"].headers())
    output.add_column(primary_key)
    
    for (original_line, matches) in operation["results"]:
        
        master_id = request.form.get(str(original_line))
        
        if master_id or operation["include_unmatched_records"]:
            out_record = trapeza.Record(matches[0].incoming.values)
            out_record.values[primary_key] = master_id or ""
			            
            for key in out_record.values:
                if key != primary_key:
                    value = request.form.get("select{0}.{1}.{2}".format(original_line, master_id, key))
                    if value == "MASTER":
                        if not operation["output_only_modified_entries"]:
                            for match in matches:
                                if match.master.record_id() == master_id:
                                    out_record.values[key] = match.master.values[header_map[key]]
                                    break
                        else:
                            out_record.values[key] = ""
                    elif value == "USER":
                        user_val = request.form.get("userentrybox{0}.{1}.{2}".format(original_line, master_id, key))
                        if value is not None and len(value) > 0:
                            out_record.values[key] = user_val
                    elif value == "INCOMING" or value == "" or value is None:
                        # If the user made no selection, assume that we're to retain the incoming data.
                        pass
                    else:
                        raise Exception("Invalid form data.")

            output.add_record(out_record)
            
    io = cStringIO.StringIO()
    
    output_function = trapeza.outputs["csv"]
    output_function(output, io, "csv")
    
    data = io.getvalue()
    io.close()
    
    response = make_response(data)
    response.headers["Content-Disposition"] = "attachment; filename=output.csv"
    
    return response

if __name__ == "__main__":
    app.debug = True
    app.run()
