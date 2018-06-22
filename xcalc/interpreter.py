from dolfin import Function, FunctionSpace, VectorElement, TensorElement
from itertools import imap, izip
import numpy as np
import ufl


# Expression which when evaluated end up in the same space as the arguments
SAME_SPACE_RULES = {ufl.algebra.Sum: np.add,
                    ufl.algebra.Abs: np.abs,
                    ufl.algebra.Division: np.divide,
                    ufl.algebra.Product: np.multiply,
                    ufl.algebra.Power: np.power,
                    ufl.mathfunctions.Sin: np.sin,
                    ufl.mathfunctions.Cos: np.cos,
                    ufl.mathfunctions.Sqrt: np.sqrt,
                    ufl.mathfunctions.Exp: np.exp, 
                    ufl.mathfunctions.Ln: np.log,
                    ufl.mathfunctions.Tan: np.tan,
                    ufl.mathfunctions.Sinh: np.sinh,
                    ufl.mathfunctions.Cosh: np.cosh,
                    ufl.mathfunctions.Tanh: np.tanh,
                    ufl.mathfunctions.Asin: np.arcsin,
                    ufl.mathfunctions.Acos: np.arccos,
                    ufl.mathfunctions.Atan: np.arctan,
                    ufl.mathfunctions.Atan2: np.arctan2}


# Expression which when evaluated end up in general in different space than 
# the arguments/require manipulations before numpy is applied
DIFFERENT_SPACE_RULES = {ufl.tensoralgebra.Inverse: np.linalg.inv,
                         ufl.tensoralgebra.Transposed: np.transpose,
                         ufl.tensoralgebra.Sym: lambda A: 0.5*(A + A.T),
                         ufl.tensoralgebra.Skew: lambda A: 0.5*(A - A.T),
                         ufl.tensoralgebra.Deviatoric: lambda A: A - np.trace(A)*np.eye(len(A))*(1./len(A)),
                         ufl.tensoralgebra.Cofactor: lambda A: np.linalg.det(A)*(np.linalg.inv(A)).T,
                         ufl.tensoralgebra.Determinant: np.linalg.det,
                         ufl.tensoralgebra.Trace: np.trace,
                         ufl.tensoralgebra.Dot: np.dot,
                         ufl.tensoralgebra.Cross: np.cross,
                         ufl.tensoralgebra.Outer: np.outer,
                         ufl.tensoralgebra.Inner: np.inner}


def Eval(expr, ss_rules=SAME_SPACE_RULES, ds_rules=DIFFERENT_SPACE_RULES):
    '''
    This intepreter translates expr into a function object or a number. Expr is 
    defined via a subset of UFL language. Letting f, g be functions in V 
    Eval(op(f, g)) is a function in V with coefs given by (op(coefs(f), coef(g))).
    '''
    # Terminals/base cases
    if isinstance(expr, (Function, int, float)):
        return expr

    if isinstance(expr, (ufl.algebra.ScalarValue, ufl.algebra.IntValue)):
        return expr.value()
        
    expr_type = type(expr)
    if expr_type in ds_rules:
        return numpy_reshaped(expr, op=ds_rules[expr_type])

    # NOTE: for now we assume that all the expression arguments are either 
    # numbers or functions in the same function space. We can get the coefficients
    # by straight forward manipulations of the coefficient arrays. The reshaping 
    # rules would be identity
    args = map(Eval, expr.ufl_operands)

    op = ss_rules[expr_type]
    # Manipulate coefs of arguments to get coefs of the expression
    coefs = map(coefs_of, args)
    V_coefs = op(*coefs)    
    # Make that function
    V = space_of(args)
    return make_function(V, V_coefs)


def make_function(V, coefs):
    '''A function in V with coefs'''
    f = Function(V)
    f.vector().set_local(coefs)
    # FIXME: parallel

    return f


def coefs_of(f):
    '''Extract coefficients of f'''
    # We arrive here either with a function or a number
    if isinstance(f, Function):
        return f.vector().get_local()
    assert isinstance(f, (int, float)), (f, type(f))

    return f


def space_of(foos):
    '''Extract the function space for representing foos'''
    # We arrive here either with a function or a number
    elm, mesh = None, None
    for f in filter(lambda x: isinstance(x, Function), foos):
        elm_ = f.function_space().ufl_element()
        mesh_ = f.function_space().mesh()

        if elm is None:
            elm = elm_
            mesh = mesh_
        else:
            assert elm_ == elm and mesh.id() == mesh_.id()

    return FunctionSpace(mesh, elm)


def numpy_reshaped(expr, op):
    '''Get the coefs by applying the numpy op to reshaped argument coefficients'''
    args = map(Eval, expr.ufl_operands)
    # Do we have V x V x ... spaces?
    sub_elm = common_sub_element([space_of((arg, )) for arg in args])
    
    get_args = []
    # Construct iterators for accesing the coef values of arguments in the 
    # right way be used with numpy op
    for arg in args:
        arg_coefs = coefs_of(arg)

        V = arg.function_space()
        shape = arg.ufl_shape

        # How to access coefficients by indices 
        indices = numpy_op_indices(V, shape)

        # Get values for op by reshaping
        if shape:
            reshape = lambda x, s=shape: x.reshape(s)
            get = (reshape(arg_coefs[index]) for index in indices)
        else:
            get = (arg_coefs[index] for index in indices)

        get_args.append(get)
    # Now all the arguments can be iterated to gether by
    args = izip(*get_args)

    # Construct the result space
    shape_res = expr.ufl_shape
    V_res = make_space(sub_elm, shape_res, V.mesh())
    # How to reshape the result and assign
    if shape_res:
        dofs = imap(list, numpy_op_indices(V_res, shape_res))
        flat = lambda x: x.flatten()
    else:
        dofs = numpy_op_indices(V_res, shape_res)
        flat = lambda x: x
        
    # Fill coefs of the result expression
    coefs_res = Function(V_res).vector().get_local()
    for dof, dof_args in izip(dofs, args):
        coefs_res[dof] = flat(op(*dof_args))

    return make_function(V_res, coefs_res)


def numpy_op_indices(V, shape):
    '''Iterator over dofs of V in a logical way'''
    # next(numpy_of_indices(V)) get indices for accesing coef of function in V
    # in a way that after reshaping the values can be used by numpy
    nsubs = V.num_sub_spaces()
    # Get will give us e.g matrix to go with det to set the value of det
    if nsubs:
        assert len(shape)
        indices = imap(list, izip(*[iter(V.sub(comp).dofmap().dofs()) for comp in range(nsubs)]))
    else:
        assert not len(shape)
        indices = iter(V.dofmap().dofs())

    return indices


def common_sub_element(spaces):
    '''V for space which are tensor products of V otherwise fail'''
    V = None
    for space in spaces:
        if not space.num_sub_spaces():
            V_ = space.ufl_element()
        else:
            V_, = set(space.ufl_element().sub_elements())
        
        assert V is None or V == V_

        V = V_
    return V


def make_space(V, shape, mesh):
    '''Tensor product space of right shape'''
    if not shape:
        elm = V
    elif len(shape) == 1:
        elm = VectorElement(V, len(shape))
    elif len(shape) == 2:
        elm = TensorElement(V, shape)
    else:
        raise ValueError('No spaces for tensor of rank 3 and higher')

    return FunctionSpace(mesh, elm)


# ------------------------------------------------------------------------------

if __name__ == '__main__':
    from dolfin import *

    mesh = UnitSquareMesh(32, 32)
    V = FunctionSpace(mesh, 'CG', 1)

    f = Expression('x[0]', degree=1)
    g = Expression('x[1]', degree=1)
    a = 1
    b = 2

    u = interpolate(f, V)
    v = interpolate(g, V)

    expr = a*u + b*v

    me = Eval(expr)
        
    true = Expression('a*f+b*g', f=f, g=g, a=a, b=b, degree=1)
            
    print assemble(inner(me-true, me-true)*dx)

    # ---- 

    mesh = UnitSquareMesh(5, 5)
    T = TensorFunctionSpace(mesh, 'DG', 0)

    u = interpolate(Constant(((0, 1), (2, 3))), T)
    expr = sym(u) + skew(u)
    true = u

    me = Eval(expr)
    print assemble(inner(me-true, me-true)*dx)

    # -----

    V = FunctionSpace(mesh, 'DG', 0)

    expr = tr(sym(u) + skew(u))

    me = Eval(expr)
    true = interpolate(Constant(3), V)
    print assemble(inner(me-true, me-true)*dx(domain=mesh))
    
    # FIXME:
    # Indexed
