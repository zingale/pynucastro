# parse the reaclib stuff

from __future__ import print_function

import glob
import os
import shutil
import re

import networkx as nx
import numpy as np
import matplotlib.pyplot as plt

from periodictable import elements

def list_unique(inlist):
    outlist = []
    for x in inlist:
        if not x in outlist:
            outlist.append(x)
    return outlist

class Nucleus(object):
    """
    a nucleus that participates in a reaction -- we store it in a
    class to hold its properties, define a sorting, and give it a
    pretty printing string

    """
    def __init__(self, name):
        self.raw = name

        # element symbol and atomic weight
        if name == "p":
            self.el = "H"
            self.A = 1
        elif name == "d":
            self.el = "H"
            self.A = 2
        elif name == "t":
            self.el = "H"
            self.A = 3
        elif name == "n":
            self.el = "n"
            self.A = 1
        else:
            e = re.match("([a-zA-Z]*)(\d*)", name)
            self.el = e.group(1).title()  # chemical symbol
            self.A = int(e.group(2))

        # atomic number comes from periodtable
        i = elements.isotope("{}-{}".format(self.A, self.el))
        self.Z = i.number
        self.N = self.A - self.Z

        # latex formatted style
        self.pretty = r"{{}}^{{{}}}\mathrm{{{}}}".format(self.A, self.el)

    def __repr__(self):
        return self.raw

    def __hash__(self):
        return hash(self.__repr__())

    def __eq__(self, other):
        return self.raw == other.raw

    def __lt__(self, other):
        if not self.Z == other.Z:
            return self.Z < other.Z
        else:
            return self.A < other.A


class Tfactors(object):
    """ precompute temperature factors for speed """

    def __init__(self, T):
        """ return the Tfactors object.  Here, T is temperature in Kelvin """
        self.T9 = T/1.e9
        self.T9i = 1.0/self.T9
        self.T913i = self.T9i**(1./3.)
        self.T913 = self.T9**(1./3.)
        self.T953 = self.T9**(5./3.)
        self.lnT9 = np.log(self.T9)


class SingleSet(object):
    """ a set in Reaclib is one piece of a rate, in the form

        lambda = exp[ a_0 + sum_{i=1}^5  a_i T_9**(2i-5)/3  + a_6 log T_9]

        A single rate in Reaclib can be composed of multiple sets
    """

    def __init__(self, a, label=None):
        """here a is iterable (e.g., list or numpy array), storing the
           coefficients, a0, ..., a6

        """
        self.a = a
        self.label = label


    def f(self):
        """
        return a function for this set -- note: Tf here is a Tfactors
        object
        """
        return lambda tf: np.exp(self.a[0] +
                                 self.a[1]*tf.T9i +
                                 self.a[2]*tf.T913i +
                                 self.a[3]*tf.T913 +
                                 self.a[4]*tf.T9 +
                                 self.a[5]*tf.T953 +
                                 self.a[6]*tf.lnT9)


    def set_string(self, prefix="set", plus_equal=False):
        """
        return a string containing the python code for this set
        """
        if plus_equal:
            string =  "{} += np.exp( ".format(prefix)
        else:
            string =  "{} = np.exp( ".format(prefix)
        string += " {}".format(self.a[0])
        if not self.a[1] == 0.0: string += " + {}*tf.T9i".format(self.a[1])
        if not self.a[2] == 0.0: string += " + {}*tf.T913i".format(self.a[2])
        if not self.a[3] == 0.0: string += " + {}*tf.T913".format(self.a[3])
        if not (self.a[4] == 0.0 and self.a[5] == 0.0 and self.a[6] == 0.0):
            string += "\n{}         ".format(len(prefix)*" ")
        if not self.a[4] == 0.0: string += " + {}*tf.T9".format(self.a[4])
        if not self.a[5] == 0.0: string += " + {}*tf.T953".format(self.a[5])
        if not self.a[6] == 0.0: string += " + {}*tf.lnT9".format(self.a[6])
        string += ")"
        return string


class Rate(object):
    """ a single Reaclib rate, which can be composed of multiple sets """

    def __init__(self, file):
        self.file = os.path.basename(file)
        self.chapter = None    # the Reaclib chapter for this reaction
        self.original_source = None   # the contents of the original rate file
        self.reactants = []
        self.products = []
        self.sets = []

        # Tells if this rate is eligible for screening
        # using screenz.f90 provided by BoxLib Microphysics.
        # If not eligible for screening, set to None
        # If eligible for screening, then
        # Rate.ion_screen is a 2-element list of Nucleus objects for screening
        self.ion_screen = None 

        idx = self.file.rfind("-")
        self.fname = self.file[:idx].replace("--","-").replace("-","_")

        self.Q = 0.0

        # read in the file, parse the different sets and store them as
        # SingleSet objects in sets[]
        f = open(file, "r")                            
        lines = f.readlines()

        self.original_source = "".join(lines)

        # first line is the chapter
        self.chapter = lines[0].strip()
        # catch table prescription
        if self.chapter != "t":
            self.chapter = int(self.chapter)

        # remove any black lines
        set_lines = [l for l in lines[1:] if not l.strip() == ""]

        if self.chapter == "t":
            # e1 -> e2, Tabulated
            s1 = set_lines.pop(0)
            s2 = set_lines.pop(0)
            s3 = set_lines.pop(0)
            s4 = set_lines.pop(0)
            s5 = set_lines.pop(0)
            f = s1.split()
            self.reactants.append(Nucleus(f[0]))
            self.products.append(Nucleus(f[1]))

            self.table_file = s2.strip()
            self.table_header_lines = int(s3.strip())
            self.table_rhoy_lines   = int(s4.strip())
            self.table_temp_lines   = int(s5.strip())
            self.table_num_vars     = 6 # Hard-coded number of variables in tables for now.
            self.table_index_name = 'j_{}_{}'.format(self.reactants[0], self.products[0])

        else:
            # the rest is the sets
            first = 1
            while len(set_lines) > 0:
                # sets are 3 lines long
                s1 = set_lines.pop(0)
                s2 = set_lines.pop(0)
                s3 = set_lines.pop(0)

                # first line of a set has up to 6 nuclei, then the label,
                # and finally the Q value
                f = s1.split()
                Q = f.pop()
                label = f.pop()

                if first:
                    self.Q = Q

                    # what's left are the nuclei -- their interpretation
                    # depends on the chapter
                    if self.chapter == 1:
                        # e1 -> e2
                        self.reactants.append(Nucleus(f[0]))
                        self.products.append(Nucleus(f[1]))

                    elif self.chapter == 2:
                        # e1 -> e2 + e3
                        self.reactants.append(Nucleus(f[0]))
                        self.products += [Nucleus(f[1]), Nucleus(f[2])]

                    elif self.chapter == 3:
                        # e1 -> e2 + e3 + e4
                        self.reactants.append(Nucleus(f[0]))
                        self.products += [Nucleus(f[1]), Nucleus(f[2]), Nucleus(f[3])]

                    elif self.chapter == 4:
                        # e1 + e2 -> e3
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1])]
                        self.products.append(Nucleus(f[2]))

                    elif self.chapter == 5:
                        # e1 + e2 -> e3 + e4
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1])]
                        self.products += [Nucleus(f[2]), Nucleus(f[3])]

                    elif self.chapter == 6:
                        # e1 + e2 -> e3 + e4 + e5
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1])]
                        self.products += [Nucleus(f[2]), Nucleus(f[3]), Nucleus(f[4])]

                    elif self.chapter == 7:
                        # e1 + e2 -> e3 + e4 + e5 + e6
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1])]
                        self.products += [Nucleus(f[2]), Nucleus(f[3]),
                                          Nucleus(f[4]), Nucleus(f[5])]

                    elif self.chapter == 8:
                        # e1 + e2 + e3 -> e4
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1]), Nucleus(f[2])]
                        self.products.append(Nucleus(f[3]))

                    elif self.chapter == 9:
                        # e1 + e2 + e3 -> e4 + e5
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1]), Nucleus(f[2])]
                        self.products += [Nucleus(f[3]), Nucleus(f[4])]

                    elif self.chapter == 10:
                        # e1 + e2 + e3 + e4 -> e5 + e6
                        self.reactants += [Nucleus(f[0]), Nucleus(f[1]),
                                           Nucleus(f[2]), Nucleus(f[3])]
                        self.products += [Nucleus(f[4]), Nucleus(f[5])]

                    elif self.chapter == 11:
                        # e1 -> e2 + e3 + e4 + e5
                        self.reactants.append(Nucleus(f[0]))
                        self.products += [Nucleus(f[1]), Nucleus(f[2]),
                                          Nucleus(f[3]), Nucleus(f[4])]
                    
                    first = 0

                # the second line contains the first 4 coefficients
                # the third lines contains the final 3
                # we can't just use split() here, since the fields run into one another
                n = 13  # length of the field
                a = [s2[i:i+n] for i in range(0, len(s2), n)]
                a += [s3[i:i+n] for i in range(0, len(s3), n)]

                a = [float(e) for e in a if not e.strip() == ""]
                self.sets.append(SingleSet(a, label=label))
                
        # compute self.prefactor and self.dens_exp from the reactants
        self.prefactor = 1.0  # this is 1/2 for rates like a + a (double counting)
        for r in list_unique(self.reactants):
            self.prefactor = self.prefactor/np.math.factorial(self.reactants.count(r))
        self.dens_exp = len(self.reactants)-1

        # determine if this rate is eligible for screening
        nucz = []
        for parent in self.reactants:
            if parent.Z != 0:
                nucz.append(parent)
        if len(nucz) > 1:
            nucz.sort(key=lambda x: x.Z)
            self.ion_screen = []
            self.ion_screen.append(nucz[0])
            self.ion_screen.append(nucz[1])
        
        self.string = ""
        self.pretty_string = r"$"
        for n, r in enumerate(self.reactants):
            self.string += "{}".format(r)
            self.pretty_string += r"{}".format(r.pretty)
            if not n == len(self.reactants)-1:
                self.string += " + "
                self.pretty_string += r" + "

        self.string += " --> "
        self.pretty_string += r" \rightarrow "

        for n, p in enumerate(self.products):
            self.string += "{}".format(p)
            self.pretty_string += r"{}".format(p.pretty)
            if not n == len(self.products)-1:
                self.string += " + "
                self.pretty_string += r" + "

        self.pretty_string += r"$"
        
    def __repr__(self):
        return self.string

    def eval(self, T):
        """ evauate the reaction rate for temperature T """
        tf = Tfactors(T)
        r = 0.0
        for s in self.sets:
            f = s.f()
            r += f(tf)

        return r

    def get_rate_exponent(self, T0):
        """
        for a rate written as a power law, r = r_0 (T/T0)**nu, return
        nu corresponding to T0
        """

        # nu = dln r /dln T, so we need dr/dT
        r1 = self.eval(T0)
        dT = 1.e-8*T0
        r2 = self.eval(T0 + dT)

        drdT = (r2 - r1)/dT
        return (T0/r1)*drdT

    def plot(self, Tmin=1.e7, Tmax=1.e10):

        T = np.logspace(np.log10(Tmin), np.log10(Tmax), 100)
        r = np.zeros_like(T)

        for n in range(len(T)):
            r[n] = self.eval(T[n])

        plt.loglog(T, r)

        plt.xlabel(r"$T$")

        if self.dens_exp == 0:
            plt.ylabel(r"\tau")
        elif self.dens_exp == 1:
            plt.ylabel(r"$N_A <\sigma v>$")
        elif self.dens_exp == 2:
            plt.ylabel(r"$N_A^2 <n_a n_b n_c v>$")

        plt.title(r"{}".format(self.pretty_string))

        plt.show()


class RateCollection(object):
    """ a collection of rates that together define a network """

    def __init__(self, rate_files):
        """
        rate_files are the files that together define the network.  This
        can be any iterable or single string, and can include
        wildcards

        """

        self.pyreaclib_dir = os.path.dirname(os.path.realpath(__file__))
        self.files = []
        self.rates = []

        if type(rate_files) is str:
            rate_files = [rate_files]

        # get the rates
        self.pyreaclib_rates_dir = os.path.join(self.pyreaclib_dir,
                                                'reaclib-rates')
        exit_program = False
        for p in rate_files:
            # check to see if the rate file is in the working dir
            fp = glob.glob(p)
            if fp:
                self.files += fp
            else:
                # check to see if the rate file is in pyreaclib/reaclib-rates
                fp = glob.glob(os.path.join(self.pyreaclib_rates_dir, p))
                if fp:
                    self.files += fp
                else: # Notify of all missing files before exiting
                    print('ERROR: File {} not found in {} or the working directory!'.format(
                        p,self.pyreaclib_rates_dir))
                    exit_program = True 
        if exit_program:
            exit()

        for rf in self.files:
            try:
                self.rates.append(Rate(rf))
            except:
                print("Error with file: {}".format(rf))
                raise
            
        # get the unique nuclei
        u = []
        for r in self.rates:
            t = list_unique(r.reactants + r.products)
            u = list_unique(u + t)

        self.unique_nuclei = sorted(u)

        # now make a list of each rate that touches each nucleus
        # we'll store this in a dictionary keyed on the nucleus
        self.nuclei_consumed = {}
        self.nuclei_produced = {}

        for n in self.unique_nuclei:
            self.nuclei_consumed[n] = []
            for r in self.rates:
                if n in r.reactants:
                    self.nuclei_consumed[n].append(r)

            self.nuclei_produced[n] = []
            for r in self.rates:
                if n in r.products:
                    self.nuclei_produced[n].append(r)

        self.tabular_rates = []
        self.reaclib_rates = []
        for n,r in enumerate(self.rates):
            if r.chapter == 't':
                self.tabular_rates.append(n)
            elif type(r.chapter)==int:
                self.reaclib_rates.append(n)
            else:
                print('ERROR: Chapter type unknown for rate chapter {}'.format(
                    str(r.chapter)))
                exit()


    def make_network(self, outfile):
        typenet_avail = {
            'python'   : Network_py,
            'sundials' : Network_sundials
        }
        base, ext = os.path.splitext(outfile)
        if ext == '.py' or outfile == 'python':
            self.output_file = outfile
            print(self.output_file)
            net = typenet_avail['python'](self)
            net.write_network(outfile)
        else:
            try:
                net = typenet_avail[outfile](self)
                net.write_network()
            except KeyError:
                print('Network type {} not available. Available networks are:')
                for k in typenet_avail.keys():
                    print(k)
                exit()

                
    def plot(self):
        G = nx.DiGraph()
        G.position={}
        G.labels = {}

        plt.plot([0,0], [8,8], 'b-')

        # nodes
        for n in self.unique_nuclei:
            G.add_node(n)
            G.position[n] = (n.N, n.Z)
            G.labels[n] = r"${}$".format(n.pretty)

        # edges
        for n in self.unique_nuclei:
            for r in self.nuclei_consumed[n]:
                for p in r.products:
                    G.add_edges_from([(n, p)])


        nx.draw_networkx_nodes(G, G.position,
                               node_color="1.0", alpha=0.4,
                               node_shape="s", node_size=1000)
        nx.draw_networkx_edges(G, G.position, edge_color="0.5")
        nx.draw_networkx_labels(G, G.position, G.labels, 
                                font_size=14, font_color="r", zorder=100)

        plt.xlim(-0.5,)
        plt.xlabel(r"$N$", fontsize="large")
        plt.ylabel(r"$Z$", fontsize="large")

        ax = plt.gca()
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.xaxis.set_ticks_position('bottom')
        ax.yaxis.set_ticks_position('left')
        plt.show()

    def __repr__(self):
        string = ""
        for r in self.rates:
            string += "{}\n".format(r.string)
        return string

    
class Network_py(RateCollection):
    def __init__(self, parent_instance_object=None):
        # Inherit all the instance attributes of
        # the parent_instance_object if it's passed.
        rem = re.compile('__(.*)__')
        if parent_instance_object:
            for d in dir(parent_instance_object):
                if not rem.match(d):
                    setattr(self, d, getattr(parent_instance_object, d))
    
    def rate_string(self, rate, indent=0, prefix="rate"):
        """
        return the functional form of rate as a function of
        the temperature (as Tfactors)

        rate is an object of class Rate
        """

        tstring = "# {}\n".format(rate.string)
        tstring += "{} = 0.0\n\n".format(prefix)

        for s in rate.sets:
            tstring += "# {}\n".format(s.label)
            tstring += "{}\n".format(s.set_string(prefix=prefix, plus_equal=True))

        string = ""
        for t in tstring.split("\n"):
            string += indent*" " + t + "\n"
        return string


    def function_string(self, rate):
        """
        return a string containing python function that computes the
        rate
        """

        string = ""
        string += "def {}(tf):\n".format(rate.fname)
        string += "{}".format(self.rate_string(rate, indent=4))
        string += "    return rate\n\n"
        return string


    def ydot_string(self, rate):
        """
        return a string containing the term in a dY/dt equation
        in a reaction network corresponding to this rate
        """

        # composition dependence
        Y_string = ""
        for n, r in enumerate(list_unique(rate.reactants)):
            c = rate.reactants.count(r)
            if c > 1:
                Y_string += "Y[i{}]**{}".format(r, c)
            else:
                Y_string += "Y[i{}]".format(r)

            if n < len(list_unique(rate.reactants))-1:
                Y_string += "*"

        # density dependence
        if rate.dens_exp == 0:
            dens_string = ""
        elif rate.dens_exp == 1:
            dens_string = "rho*"
        else:
            dens_string = "rho**{}*".format(rate.dens_exp)

        # prefactor
        if not rate.prefactor == 1.0:
            prefactor_string = "{:0.15f}*".format(rate.prefactor)
        else:
            prefactor_string = ""

        return "{}{}{}*lambda_{}".format(prefactor_string, dens_string,
                                         Y_string, rate.fname)

    def jacobian_string(self, rate, ydot_j, y_i):
        """
        return a string containing the term in a jacobian matrix 
        in a reaction network corresponding to this rate

        Returns the derivative of the j-th YDOT wrt. the i-th Y
        If the derivative is zero, returns the empty string ''

        ydot_j and y_i are objects of the class 'Nucleus'
        """
        if ((ydot_j not in rate.reactants and ydot_j not in rate.products) or
            y_i not in rate.reactants):
            return ''

        # composition dependence
        Y_string = ""
        for n, r in enumerate(list_unique(rate.reactants)):
            c = rate.reactants.count(r)
            if y_i == r:
                if c == 1:
                    continue
                if n>0 and n < len(list_unique(rate.reactants))-1:
                    Y_string += "*"
                if c > 2:
                    Y_string += "{}*Y[i{}]**{}".format(c, r, c-1)
                elif c==2:
                    Y_string += "2*Y[i{}]".format(r)
            else:
                if n>0 and n < len(list_unique(rate.reactants))-1:
                    Y_string += "*"
                if c > 1:
                    Y_string += "Y[i{}]**{}".format(r, c)
                else:
                    Y_string += "Y[i{}]".format(r)

        # density dependence
        if rate.dens_exp == 0:
            dens_string = ""
        elif rate.dens_exp == 1:
            dens_string = "rho*"
        else:
            dens_string = "rho**{}*".format(rate.dens_exp)

        # prefactor
        if not rate.prefactor == 1.0:
            prefactor_string = "{:0.15f}*".format(rate.prefactor)
        else:
            prefactor_string = ""

        if Y_string=="" and dens_string=="" and prefactor_string=="":
            rstring = "{}{}{}lambda_{}"
        else:
            rstring = "{}{}{}*lambda_{}"
        return rstring.format(prefactor_string, dens_string, Y_string, rate.fname)

    def print_network_overview(self, rate):
        for n in self.unique_nuclei:
            print(n)
            print("  consumed by: ")
            for r in self.nuclei_consumed[n]:
                print("     {} : {}".format(r.string, self.ydot_string(r)))

            print("  produced by: ")
            for r in self.nuclei_produced[n]:
                print("     {} : {}".format(r.string, self.ydot_string(r)))

            print(" ")
    
    def write_network(self, outfile):
        """
        this is the actual RHS for the system of ODEs that
        this network describes
        """
        try: of = open(outfile, "w")
        except: raise

        of.write("import numpy as np\n")
        of.write("import reaclib\n\n")

        # integer keys
        for i, n in enumerate(self.unique_nuclei):
            of.write("i{} = {}\n".format(n, i))

        of.write("nnuc = {}\n\n".format(len(self.unique_nuclei)))

        of.write("A = np.zeros((nnuc), dtype=np.int32)\n\n")
        for n in self.unique_nuclei:
            of.write("A[i{}] = {}\n".format(n, n.A))

        of.write("\n")

        for r in self.rates:
            of.write(self.function_string(r))

        of.write("def rhs(t, Y, rho, T):\n\n")

        indent = 4*" "

        # get the rates
        of.write("{}tf = reaclib.Tfactors(T)\n\n".format(indent))
        for r in self.rates:
            of.write("{}lambda_{} = {}(tf)\n".format(indent, r.fname, r.fname))

        of.write("\n")

        of.write("{}dYdt = np.zeros((nnuc), dtype=np.float64)\n\n".format(indent))

        # now make the RHSs
        for n in self.unique_nuclei:
            of.write("{}dYdt[i{}] = (\n".format(indent, n))
            for r in self.nuclei_consumed[n]:
                c = r.reactants.count(n)
                if c == 1:
                    of.write("{}   -{}\n".format(indent, self.ydot_string(r)))
                else:
                    of.write("{}   -{}*{}\n".format(indent, c, self.ydot_string(r)))
            for r in self.nuclei_produced[n]:
                c = r.products.count(n)
                if c == 1:
                    of.write("{}   +{}\n".format(indent, self.ydot_string(r)))
                else:
                    of.write("{}   +{}*{}\n".format(indent, c, self.ydot_string(r)))
            of.write("{}   )\n\n".format(indent))

        of.write("{}return dYdt\n".format(indent))

    
class Network_f90(RateCollection):
    def __init__(self):
        self.ftags = {}
        self.ftags['<nreact>'] = self.nreact
        self.ftags['<nspec>'] = self.nspec
        self.ftags['<net_ynuc>'] = self.ynuc
        self.ftags['<nrxn>'] = self.nrxn
        self.ftags['<jion>'] = self.jion
        self.ftags['<ebind>'] = self.ebind
        self.ftags['<aion>'] = self.aion
        self.ftags['<zion>'] = self.zion
        self.ftags['<ctemp_declare>'] = self.ctemp_declare
        self.ftags['<rmul>'] = self.rmul
        self.ftags['<screen_logical>'] = self.screen_logical
        self.ftags['<screen_add>'] = self.screen_add
        self.ftags['<ctemp_ptr_declare>'] = self.ctemp_ptr_declare
        self.ftags['<ctemp_allocate>'] = self.ctemp_allocate
        self.ftags['<ctemp_deallocate>'] = self.ctemp_deallocate
        self.ftags['<ctemp_switch>'] = self.ctemp_switch
        self.ftags['<table_num>'] = self.table_num
        self.ftags['<table_indices>'] = self.table_indices
        self.ftags['<table_init_meta>'] = self.table_init_meta
        self.ftags['<ydot>'] = self.ydot
        self.ftags['<enuc_dqweak>'] = self.enuc_dqweak
        self.ftags['<enuc_epart>'] = self.enuc_epart
        self.ftags['<jacobian>'] = self.jacobian
        self.ftags['<yinit_nuc>'] = self.yinit_nuc
        self.ftags['<final_net_print>'] = self.final_net_print
        self.ftags['<headerline>'] = self.headerline
        self.ftags['<cvodeneq>'] = self.cvodeneq
        self.ftags['<net_ymass_init>'] = self.net_ymass_init
        self.indent = '  '

    def ydot_string(self, rate):
        """
        return a string containing the term in a dY/dt equation
        in a reaction network corresponding to this rate for Fortran 90.
        """

        # composition dependence
        Y_string = ""
        for n, r in enumerate(list_unique(rate.reactants)):
            c = rate.reactants.count(r)
            if c > 1:
                Y_string += "Y(j{})**{}".format(r, c)
            else:
                Y_string += "Y(j{})".format(r)

            if n < len(list_unique(rate.reactants))-1:
                Y_string += " * "

        # density dependence
        if rate.dens_exp == 0:
            dens_string = ""
        elif rate.dens_exp == 1:
            dens_string = "dens * "
        else:
            dens_string = "dens**{} * ".format(rate.dens_exp)

        # prefactor
        if not rate.prefactor == 1.0:
            prefactor_string = "{:0.15f} * ".format(rate.prefactor)
        else:
            prefactor_string = ""

        return "{}{}{} * reactvec(i_scor, k_{}) * reactvec(i_rate, k_{})".format(
            prefactor_string,
            dens_string,
            Y_string,
            rate.fname,
            rate.fname)

    def jacobian_string(self, rate, ydot_j, y_i):
        """
        return a string containing the term in a jacobian matrix 
        in a reaction network corresponding to this rate

        Returns the derivative of the j-th YDOT wrt. the i-th Y
        If the derivative is zero, returns the empty string ''

        ydot_j and y_i are objects of the class 'Nucleus'
        """
        if ((ydot_j not in rate.reactants and ydot_j not in rate.products) or
            y_i not in rate.reactants):
            return ''

        # composition dependence
        Y_string = ""
        for n, r in enumerate(list_unique(rate.reactants)):
            c = rate.reactants.count(r)
            if y_i == r:
                if c == 1:
                    continue
                if n>0 and n < len(list_unique(rate.reactants))-1:
                    Y_string += "*"
                if c > 2:
                    Y_string += "{}*Y(j{})**{}".format(c, r, c-1)
                elif c==2:
                    Y_string += "2*Y(j{})".format(r)
            else:
                if n>0 and n < len(list_unique(rate.reactants))-1:
                    Y_string += "*"
                if c > 1:
                    Y_string += "Y(j{})**{}".format(r, c)
                else:
                    Y_string += "Y(j{})".format(r)

        # density dependence
        if rate.dens_exp == 0:
            dens_string = ""
        elif rate.dens_exp == 1:
            dens_string = "dens * "
        else:
            dens_string = "dens**{} * ".format(rate.dens_exp)

        # prefactor
        if not rate.prefactor == 1.0:
            prefactor_string = "{:0.15f} * ".format(rate.prefactor)
        else:
            prefactor_string = ""

        if Y_string=="" and dens_string=="" and prefactor_string=="":
            rstring = "{}{}{}   reactvec(i_scor, k_{}) * reactvec(i_rate, k_{})"
        else:
            rstring = "{}{}{} * reactvec(i_scor, k_{}) * reactvec(i_rate, k_{})"
        return rstring.format(prefactor_string, dens_string, Y_string,
                              rate.fname, rate.fname)

    
    def io_open(self, infile, outfile):
        try: of = open(outfile, "w")
        except: raise
        try: ifile = open(infile, 'r')
        except: raise
        return (ifile, of)

    def io_close(self, infile, outfile):
        infile.close()
        outfile.close()
        
    def fmt_to_dp_f90(self, i):
        return '{:1.6e}'.format(float(i)).replace('e','d')

    def get_indent_amt(self, l, k):
        rem = re.match('\A'+k+'\(([0-9]*)\)\Z',l)
        return int(rem.group(1))

    def write_network(self):
        """
        This writes the RHS, jacobian and ancillary files for the system of ODEs that
        this network describes, using the template files.
        """

        for tfile in self.template_files:
            tfile_basename = os.path.basename(tfile)
            outfile    = tfile_basename.replace('.template', '')
            ifile, of = self.io_open(tfile, outfile)
            for l in ifile:
                ls = l.strip()
                foundkey = False
                for k in self.ftags.keys():
                    if k in ls:
                        foundkey = True
                        n_indent = self.get_indent_amt(ls, k)
                        self.ftags[k](n_indent, of)
                if not foundkey:
                    of.write(l)    
            self.io_close(ifile, of)

    def nreact(self, n_indent, of):
        of.write('{}integer, parameter :: nreact = {}\n'.format(
            self.indent*n_indent,
            len(self.rates)))

    def nspec(self, n_indent, of):
        of.write('{}integer, parameter :: nspec = {}\n'.format(
            self.indent*n_indent,
            len(self.unique_nuclei)))
        
    def ynuc(self, n_indent, of):
        for nuc in self.unique_nuclei:
            of.write('{}double precision :: y{}\n'.format(
                self.indent*n_indent, nuc))

    def jion(self, n_indent, of):
        for i,nuc in enumerate(self.unique_nuclei):
            of.write('{}integer :: j{}   = {}\n'.format(
                self.indent*n_indent, nuc, i+1))
        of.write('{}! Energy Generation Rate\n'.format(
            self.indent*n_indent))
        of.write('{}integer :: jenuc   = {}\n'.format(
            self.indent*n_indent,
            len(self.unique_nuclei)+1))

    def nrxn(self, n_indent, of):
        for i,r in enumerate(self.rates):
            of.write('{}integer :: k_{}   = {}\n'.format(
                self.indent*n_indent, r.fname, i+1))

    def ebind(self, n_indent, of):
        for nuc in self.unique_nuclei:
            of.write('{}ebind_per_nucleon(j{})   = 0.0d0\n'.format(
                self.indent*n_indent,
                nuc))

    def aion(self, n_indent, of):
        for nuc in self.unique_nuclei:
            of.write('{}aion(j{})   = {}\n'.format(
                self.indent*n_indent,
                nuc,
                self.fmt_to_dp_f90(nuc.A)))

    def zion(self, n_indent, of):
        for nuc in self.unique_nuclei:
            of.write('{}zion(j{})   = {}\n'.format(
                self.indent*n_indent,
                nuc,
                self.fmt_to_dp_f90(nuc.Z)))
            
    def ctemp_declare(self, n_indent, of):
        for n in self.reaclib_rates:
            of.write('{}double precision, target, dimension(:,:), allocatable :: ctemp_rate_{}\n'.format(self.indent*n_indent, n+1))

    def rmul(self, n_indent, of):
        for i,r in enumerate(self.rates):
            of.write('{}{}'.format(self.indent*n_indent, len(r.sets)))
            if i==len(self.rates)-1:
                of.write(' /)\n')
            else:
                of.write(', &\n')

    def screen_logical(self, n_indent, of):
        for i, r in enumerate(self.rates):
            if r.ion_screen:
                of.write('{}{}'.format(self.indent*n_indent, '.true.'))
            else:
                of.write('{}{}'.format(self.indent*n_indent, '.false.'))
            if i==len(self.rates)-1:
                of.write(' /)\n')
            else:
                of.write(', &\n')

    def screen_add(self, n_indent, of):
        for i, r in enumerate(self.rates):
            if r.ion_screen:
                of.write('{}call add_screening_factor('.format(self.indent*n_indent))
                of.write('zion(j{}), aion(j{}), &\n'.format(r.ion_screen[0],
                                                            r.ion_screen[0]))
                of.write('{}zion(j{}), aion(j{}))\n\n'.format(self.indent*(n_indent+1),
                                                              r.ion_screen[1],
                                                              r.ion_screen[1]))

    def ctemp_ptr_declare(self, n_indent, of):
        of.write('{}type(ctemp_ptr), dimension({}) :: ctemp_point\n'.format(
            self.indent*n_indent,
            len(self.reaclib_rates)))
                
    def ctemp_allocate(self, n_indent, of):
        for nr in self.reaclib_rates:
            r = self.rates[nr]
            of.write('{}allocate( ctemp_rate_{}(7, rate_mult({})) )\n'.format(
                self.indent*n_indent, nr+1, nr+1))
            of.write('{}! {}\n'.format(self.indent*n_indent, r.fname))
            for ns,s in enumerate(r.sets):
                of.write('{}ctemp_rate_{}(:, {}) = (/  &\n'.format(
                    self.indent*n_indent, nr+1, ns+1))
                for na,an in enumerate(s.a):
                    of.write('{}{}'.format(self.indent*n_indent*2,
                                           self.fmt_to_dp_f90(an)))
                    if na==len(s.a)-1:
                        of.write(' /)\n')
                    else:
                        of.write(', &\n')
                of.write('\n')
        if len(self.tabular_rates) > 0:
            of.write('{}call init_table_meta()\n'.format(self.indent*n_indent))
        of.write('\n')

    def ctemp_deallocate(self, n_indent, of):
        for nr in self.reaclib_rates:
            of.write('{}deallocate( ctemp_rate_{} )\n'.format(
                self.indent*n_indent, nr+1))

    def ctemp_switch(self, n_indent, of):
        for nr,r in enumerate(self.rates):
            of.write('{}'.format(self.indent*n_indent))
            if nr!=0:
                of.write('else ')
            of.write('if (iwhich == {}) then\n'.format(nr+1))
            if nr in self.reaclib_rates:
                of.write('{}ctemp => ctemp_rate_{}\n'.format(
                    self.indent*(n_indent+1), nr+1))
            elif nr in self.tabular_rates:
                of.write(
                    '{}call table_meta({})%bl_lookup(rhoy, temp, jtab_rate, rate)\n'.format(
                        self.indent*(n_indent+1), r.table_index_name))
                of.write('{}return_from_table = .true.\n'.format(self.indent*(n_indent+1)))
            else:
                print('ERROR: rate not in self.reaclib_rates or self.tabular_rates!')
                exit()
        of.write('{}end if\n'.format(self.indent*n_indent))

    def table_num(self, n_indent, of):
        of.write('{}integer, parameter :: num_tables   = {}\n'.format(
            self.indent*n_indent, len(self.tabular_rates)))

    def table_indices(self, n_indent, of):
        for n,irate in enumerate(self.tabular_rates):
            r = self.rates[irate]
            of.write('{}integer, parameter :: {}   = {}\n'.format(
                self.indent*n_indent, r.table_index_name, n+1))

    def table_init_meta(self, n_indent, of):
        for n,irate in enumerate(self.tabular_rates):
            r = self.rates[irate]
            of.write('{}table_meta({})%rate_table_file = \'{}\'\n'.format(
                self.indent*n_indent, r.table_index_name, r.table_file))
            of.write('{}table_meta({})%num_header = {}\n'.format(
                self.indent*n_indent, r.table_index_name, r.table_header_lines))
            of.write('{}table_meta({})%num_rhoy = {}\n'.format(
                self.indent*n_indent, r.table_index_name, r.table_rhoy_lines))
            of.write('{}table_meta({})%num_temp = {}\n'.format(
                self.indent*n_indent, r.table_index_name, r.table_temp_lines))
            of.write('{}table_meta({})%num_vars = {}\n'.format(
                self.indent*n_indent, r.table_index_name, r.table_num_vars))
            of.write('\n')

    def ydot(self, n_indent, of):
        # now make the RHSs
        for n in self.unique_nuclei:
            of.write("{}YDOT(j{}) = ( &\n".format(self.indent*n_indent, n))
            for r in self.nuclei_consumed[n]:
                c = r.reactants.count(n)
                if c == 1:
                    of.write("{}   - {} &\n".format(self.indent*n_indent,
                                                    self.ydot_string(r)))
                else:
                    of.write("{}   - {} * {} &\n".format(self.indent*n_indent,
                                                         c, self.ydot_string(r)))
            for r in self.nuclei_produced[n]:
                c = r.products.count(n)
                if c == 1:
                    of.write("{}   + {} &\n".format(self.indent*n_indent,
                                                    self.ydot_string(r)))
                else:
                    of.write("{}   + {} * {} &\n".format(self.indent*n_indent,
                                                         c, self.ydot_string(r)))
            of.write("{}   )\n\n".format(self.indent*n_indent))

    def enuc_dqweak(self, n_indent, of):
        # Add tabular dQ corrections to the energy generation rate
        for nr, r in enumerate(self.rates):
            if nr in self.tabular_rates:
                if len(r.reactants) != 1:
                    print('ERROR: Unknown tabular dQ corrections for a reaction where the number of reactants is not 1.')
                    exit()
                else:
                    reactant = r.reactants[0]
                    of.write('{}YDOT(jenuc) = YDOT(jenuc) + N_AVO * YDOT(j{}) * reactvec(i_dqweak, k_{})\n'.format(self.indent*n_indent, reactant, r.fname))
        
    def enuc_epart(self, n_indent, of):
        # Add particle energy generation rates (gamma heating and neutrino loss from decays)
        # to the energy generation rate (doesn't include plasma neutrino losses)
        for nr, r in enumerate(self.rates):
            if nr in self.tabular_rates:
                if len(r.reactants) != 1:
                    print('ERROR: Unknown particle energy corrections for a reaction where the number of reactants is not 1.')
                    exit()
                else:
                    reactant = r.reactants[0]
                    of.write('{}YDOT(jenuc) = YDOT(jenuc) + N_AVO * Y(j{}) * reactvec(i_epart, k_{})\n'.format(self.indent*n_indent, reactant, r.fname))
        
    def jacobian(self, n_indent, of):
        # now make the JACOBIAN
        for nj in self.unique_nuclei:
            for ni in self.unique_nuclei:
                jac_identically_zero = True
                of.write("{}DJAC(j{},j{}) = ( &\n".format(
                    self.indent*n_indent, nj, ni))
                for r in self.nuclei_consumed[nj]:
                    sjac = self.jacobian_string(r, nj, ni)
                    if sjac != '':
                        jac_identically_zero = False
                        c = r.reactants.count(nj)
                        if c == 1:
                            of.write("{}   - {} &\n".format(
                                self.indent*n_indent, sjac))
                        else:
                            of.write("{}   - {} * {} &\n".format(self.indent*n_indent,
                                                                 c, sjac))
                for r in self.nuclei_produced[nj]:
                    sjac = self.jacobian_string(r, nj, ni)
                    if sjac != '':
                        jac_identically_zero = False
                        c = r.products.count(nj)
                        if c == 1:
                            of.write("{}   + {} &\n".format(
                                self.indent*n_indent, sjac))
                        else:
                            of.write("{}   + {} * {} &\n".format(self.indent*n_indent,
                                                                 c, sjac))

                if jac_identically_zero:
                    of.write("{}   + {} &\n".format(self.indent*n_indent, '0.0d0'))
                of.write("{}   )\n\n".format(self.indent*n_indent))

    def yinit_nuc(self, n_indent, of):
        for n in self.unique_nuclei:
            of.write("{}cv_data%Y0(j{})   = net_initial_abundances%y{}\n".format(
                self.indent*n_indent, n, n))        

    def final_net_print(self, n_indent, of):
        for n in self.unique_nuclei:
            of.write("{}write(*,'(A,ES25.14)') '{}: ', cv_data%Y(j{})\n".format(
                self.indent*n_indent, n, n))

    def headerline(self, n_indent, of):
        of.write('{}write(2, fmt=hfmt) '.format(self.indent*n_indent))
        for nuc in self.unique_nuclei:
            of.write("'Y_{}', ".format(nuc))
        of.write("'E_nuc', 'Time'\n")

    def cvodeneq(self, n_indent, of):
        of.write('{} '.format(self.indent*n_indent))
        of.write('integer*8 :: NEQ = {} ! Size of ODE system\n'.format(
            len(self.unique_nuclei)+1))

    def net_ymass_init(self, n_indent, of):
        for n in self.unique_nuclei:
            of.write('{}net_initial_abundances%y{} = 0.0d0\n'.format(
                self.indent*n_indent, n))


class Network_sundials(Network_f90):
    def __init__(self, parent_instance_object=None):
        # Inherit all the instance attributes of
        # the parent_instance_object if it's passed.
        rem = re.compile('__(.*)__')
        if parent_instance_object:
            for d in dir(parent_instance_object):
                if not rem.match(d):
                    setattr(self, d, getattr(parent_instance_object, d))

        # Initialize Network_f90 stuff
        Network_f90.__init__(self)

        # Set up some directories
        self.sundials_dir = os.path.join(self.pyreaclib_dir,
                                    'templates',
                                    'sundials-cvode')
        self.template_file_select = os.path.join(self.sundials_dir,
                                            '*.template')
        self.template_files = glob.glob(self.template_file_select)


    
class Network_boxlib(Network_f90):
    def __init__(self, parent_instance_object=None):
        # Inherit all the instance attributes of
        # the parent_instance_object if it's passed.
        rem = re.compile('__(.*)__')
        if parent_instance_object:
            for d in dir(parent_instance_object):
                if not rem.match(d):
                    setattr(self, d, getattr(parent_instance_object, d))

        # Initialize Network_f90 stuff
        Network_f90.__init__(self)
                    
        # Set up some directories
        self.boxlib_dir = os.path.join(self.pyreaclib_dir,
                                       'templates',
                                       'boxlib')
        self.template_file_select = os.path.join(self.boxlib_dir,
                                                 '*.template')
        self.template_files = glob.glob(self.template_file_select)

        
if __name__ == "__main__":
    r = Rate("examples/CNO/c13-pg-n14-nacr")
    print(r.rate_string(indent=3))
    print(r.eval(1.0e9))
    print(r.get_rate_exponent(2.e7))


    rc = RateCollection("examples/CNO/*-*")
    print(rc)
    rc.print_network_overview()
    rc.make_network()
