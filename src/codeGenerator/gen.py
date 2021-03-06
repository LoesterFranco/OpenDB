#!/usr/bin/python3

from jinja2 import Template, Environment, FileSystemLoader
import subprocess
from subprocess import call, run
import argparse
from helper import *
from parser import Parser
import os
import shutil

parser = argparse.ArgumentParser(description='Code generator')
parser.add_argument('--json', action='store', required=True)
parser.add_argument('--src_dir', action='store', required=True)
parser.add_argument('--include_dir', action='store', required=True)
parser.add_argument('--impl', action='store', required=True)

args = parser.parse_args()

src = args.json
srcDir = args.src_dir
includeDir = args.include_dir
impl = args.impl

with open(src) as file:
    schema = json.load(file)
    file.close()

env = Environment(
    loader = FileSystemLoader(impl),
    trim_blocks = True
)

#Creating Directory for generated files

if(os.path.exists('generated')):
    shutil.rmtree('generated')
os.mkdir('generated')

toBeMerged = []
     
    
print('###################Code Generation Begin###################')
addOnceToDict([
    'classes',
    'iterators'],
    schema
)

for i, klass in enumerate(schema['classes']):
    if 'src' in klass:
        with open(klass['src']) as file:
            klass = json.load(file)
    addOnceToDict([
        'classes',
        'fields',
        'constructors',
        'enums',
        'structs',
        'h_includes',
        'cpp_includes'],
        klass)
    schema['classes'][i]=klass

if "relations" in schema:
    for relation in schema['relations']:
        if relation['type']=='n_1':
            relation['first'], relation['second'] = relation['second'], relation['first']
            relation['type'] = '1_n'
        if relation['type'] != '1_n':
            raise KeyError('relation type is not supported, use either 1_n or n_1')
        parent = getClassIndex(schema, relation['first'])
        child = getClassIndex(schema, relation['second'])
        if parent == -1:
            raise NameError('Class {} in relations is not found'.format(relation['first']))
        if child == -1:
            raise NameError('Class {} in relations is not found'.format(relation['second']))
        inParentField = {}
        inParentField['name'] = getTableName(relation['second'])
        inParentField['type'] = relation['second']
        inParentField['table'] = True
        inParentField['dbSetGetter'] = True
        inParentField['components'] = [inParentField['name']]
        inParentField['flags'] = ["cmp","serial", "diff", "set", "get"]
        
        schema['classes'][parent]['fields'].append(inParentField)
        schema['classes'][parent]['cpp_includes'].extend([
            '{}.h'.format(relation['second']),
            'dbSet.h'
        ])

        childTypeName =  '_{}'.format(relation['second'])

        if childTypeName not in schema['classes'][parent]['classes']:
            schema['classes'][parent]['classes'].append(childTypeName)

        if 'dbTable' not in schema['classes'][parent]['classes']:
            schema['classes'][parent]['classes'].append('dbTable')

  
        
for klass in schema['classes']:

    
    #Adding functional name to fields and extracting field components
    struct = {"name":"{}Flags".format(klass['name']),"fields":[]}

    flag_num_bits = 0
    for field in klass['fields']:
        if 'bits' in field:
            struct['fields'].append(field)
            flag_num_bits += int(field['bits'])
        field['bitFields'] = isBitFields(field, klass['structs'])
        field['isStruct'] = (getStruct(field['type'],klass['structs']) is not None)

        field['isRef'] = isRef(field['type']) if field.get('parent') is not None else False
        field['refType'] = getRefType(field['type'])
        field['isHashTable'] = isHashTable(field['type'])
        field['hashTableType'] = getHashTableType(field['type'])


        # Check if a class is being used inside a template definition to add to the list of forward declared classes
        
        templateClassName = None
        tmp = getTemplateType(field['type'])
        while tmp is not None:
            templateClassName = tmp
            tmp = getTemplateType(tmp)
            

        if templateClassName is not None:
            if templateClassName not in klass['classes'] and klass['name'] != templateClassName[1:]:
                klass['classes'].append(templateClassName)



        if field.get('table', False):
            if field['type'].startswith('db'):
                field['functional_name'] = '{}s'.format(field['type'][2:])
            else:
                field['functional_name'] = '{}s'.format(field['type'])
            field['components'] = [field['name']]
        else:
            field['functional_name'] = getFunctionalName(field['name'])
            field['components'] = components(klass['structs'], field['name'], field['type'])
        
        field['setterFunctionName'] = 'set' + field['functional_name']
        field['getterFunctionName'] = ('is' if field['type'] == 'bool' or field.get('bits') == 1 else 'get') + field['functional_name']

        if field['isRef']:
            field['setterArgumentType'] = field['getterReturnType'] = field['refType']
        elif field['isHashTable']:
            if 'no-set' not in field['flags']:
                field.append('no-set')
            field['setterArgumentType'] = field['getterReturnType'] = field['hashTableType']
            field['getterFunctionName'] = "find"+ field['setterArgumentType'][3:-1]
        elif 'bits' in field and field['bits'] == 1:
            field['setterArgumentType'] = field['getterReturnType'] = 'bool'
        else:
            field['setterArgumentType'] = field['getterReturnType'] = field['type']


    klass['fields'] = [field for field in klass['fields'] if 'bits' not in field]
    
    if flag_num_bits > 0 and flag_num_bits % 32 != 0:
        spare_bits_field = {
            "name": "_spare_bits",
            "type": "uint",
            "bits": 32 - (flag_num_bits%32),
            "flags": ["no-cmp", "no-set", "no-get", "no-serial", "no-diff"]
        } 
        struct['fields'].append(spare_bits_field)
        
    if len(struct['fields'])>0:

        struct['in_class'] = True,
        struct['in_class_name'] = '_flags'
        klass['structs'].insert(0,struct)
        klass['fields'].insert(0,{
            'name':'_flags',
            'type':struct['name'],
            'components':components(klass['structs'], '_flags', struct['name']),
            'bitFields':True,
            'isStruct':True,
            'flags': ["no-cmp", "no-set", "no-get", "no-serial", "no-diff"]
        })
        
    #Generating files
    for template_file in ['impl.h','impl.cpp']:
        template = env.get_template(template_file)
        text = template.render(klass = klass, schema = schema)
        fileType = template_file.split('.')
        out_file = '{}.{}'.format(klass['name'], template_file.split('.')[1])
        toBeMerged.append(out_file)
        out_file = os.path.join('generated', out_file)
        with open(out_file, 'w') as file:
            file.write(text)
        
    
includes = ['db.h','dbObject.h']
for template_file in ['db.h','dbObject.h','CMakeLists.txt','dbObject.cpp']:
    template = env.get_template(template_file)
    text = template.render(schema = schema)
    out_file = os.path.join('generated', template_file)
    toBeMerged.append(template_file)
    with open(out_file, 'w') as file:
        file.write(text)
    
        
# Generating all iterators
for itr in schema['iterators']:
    for template_file in ['itr.h', 'itr.cpp']:
        template = env.get_template(template_file)
        text = template.render(itr = itr, schema = schema)
        out_file = '{}.{}'.format(itr['name'],template_file.split('.')[1])
        toBeMerged.append(out_file)
        out_file = os.path.join('generated', out_file)
        with open(out_file, 'w') as file:
            file.write(text)
              

#Merging with existing files
for item in toBeMerged:
    if item in includes:
        dr = includeDir
    else:
        dr = srcDir
    if os.path.exists(os.path.join(dr, item)):
        p = Parser(os.path.join(dr, item))
        if item == 'CMakeLists.txt':
            p.setCommentStr('#')
        assert(p.parseUserCode())
        assert(p.cleanCode())
        assert(p.parseSourceCode(os.path.join('generated', item)))
        assert(p.writeInFile(os.path.join(dr, item)))
    else:
        with open(os.path.join('generated', item),'r') as read, open(os.path.join(dr, item),'w') as out:
            text = read.read()
            out.write(text)
    if item != 'CMakeLists.txt':
        cf = ['clang-format', '-i', os.path.join(dr, item)]
        retcode = call(cf)
        if retcode != 0:
            print("Failed to format {}".format(os.path.join(dr, item)))
    print('Generated: ',os.path.join(dr, item))
shutil.rmtree('generated')
print('###################Code Generation End###################')