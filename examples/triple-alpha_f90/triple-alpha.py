# triple-alpha rate module generator for a fortran network
import pyreaclib

files = ["c12-gaa-he4-fy05",
         "he4-aag-c12-fy05"]

pyreaclib.make_network(files, 'sundials')




